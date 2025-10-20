"""Optimized batch insert service for high-performance data ingestion."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, TypeVar, Generic

import pandas as pd
from loguru import logger
from sqlalchemy import Table, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from longport_quant.persistence.db import DatabaseSessionManager


T = TypeVar("T")


@dataclass
class BatchConfig:
    """Configuration for batch operations."""

    batch_size: int = 1000
    max_retries: int = 3
    retry_delay: float = 1.0
    use_copy_from: bool = True  # Use PostgreSQL COPY for faster inserts
    conflict_action: str = "update"  # update, ignore, or error
    chunk_size: int = 10000  # For processing large datasets


class BatchInsertService(Generic[T]):
    """
    Optimized batch insert service with multiple strategies.

    Features:
    - Bulk insert with configurable batch sizes
    - PostgreSQL COPY support for maximum performance
    - Automatic retry on failure
    - Progress tracking
    - Memory-efficient chunking
    """

    def __init__(
        self,
        db: DatabaseSessionManager,
        config: Optional[BatchConfig] = None
    ):
        """Initialize batch insert service."""
        self.db = db
        self.config = config or BatchConfig()
        self._stats = {
            "total_inserted": 0,
            "total_updated": 0,
            "total_skipped": 0,
            "total_errors": 0,
            "last_batch_time": None,
            "avg_records_per_second": 0
        }

    async def bulk_insert_klines_optimized(
        self,
        table_name: str,
        records: List[Dict[str, Any]],
        conflict_columns: List[str]
    ) -> Dict[str, int]:
        """
        Optimized bulk insert for K-line data using multiple strategies.

        Args:
            table_name: Name of the target table
            records: List of records to insert
            conflict_columns: Columns that define uniqueness

        Returns:
            Dictionary with insert statistics
        """
        if not records:
            logger.warning("No records to insert")
            return self._stats

        logger.info(f"Starting bulk insert of {len(records)} records into {table_name}")
        start_time = datetime.now()

        # Choose strategy based on data size and configuration
        if self.config.use_copy_from and len(records) > 5000:
            # Use COPY for large datasets
            result = await self._bulk_copy_from(table_name, records)
        else:
            # Use batch insert for smaller datasets
            result = await self._batch_insert_on_conflict(
                table_name, records, conflict_columns
            )

        # Update statistics
        elapsed = (datetime.now() - start_time).total_seconds()
        if elapsed > 0:
            self._stats["avg_records_per_second"] = len(records) / elapsed
        self._stats["last_batch_time"] = datetime.now()

        logger.info(
            f"Completed bulk insert: {self._stats['total_inserted']} inserted, "
            f"{self._stats['total_updated']} updated, {self._stats['total_skipped']} skipped "
            f"({self._stats['avg_records_per_second']:.0f} records/sec)"
        )

        return self._stats

    async def _batch_insert_on_conflict(
        self,
        table_name: str,
        records: List[Dict[str, Any]],
        conflict_columns: List[str]
    ) -> Dict[str, int]:
        """Batch insert with ON CONFLICT handling."""
        total_processed = 0

        # Process in chunks
        for chunk_start in range(0, len(records), self.config.chunk_size):
            chunk_end = min(chunk_start + self.config.chunk_size, len(records))
            chunk = records[chunk_start:chunk_end]

            # Process chunk in batches
            for i in range(0, len(chunk), self.config.batch_size):
                batch = chunk[i:i + self.config.batch_size]

                retry_count = 0
                while retry_count < self.config.max_retries:
                    try:
                        async with self.db.session() as session:
                            async with session.begin():
                                # Build bulk insert statement
                                if table_name == "kline_daily":
                                    from longport_quant.persistence.models import KlineDaily
                                    await self._execute_batch_insert(
                                        session, KlineDaily, batch, conflict_columns
                                    )
                                elif table_name == "kline_minute":
                                    from longport_quant.persistence.models import KlineMinute
                                    await self._execute_batch_insert(
                                        session, KlineMinute, batch, conflict_columns
                                    )
                                else:
                                    # Generic table insert
                                    await self._execute_generic_insert(
                                        session, table_name, batch, conflict_columns
                                    )

                                total_processed += len(batch)
                                self._stats["total_inserted"] += len(batch)

                        break  # Success, exit retry loop

                    except Exception as e:
                        retry_count += 1
                        if retry_count >= self.config.max_retries:
                            logger.error(
                                f"Failed to insert batch after {self.config.max_retries} retries: {e}"
                            )
                            self._stats["total_errors"] += len(batch)
                        else:
                            logger.warning(
                                f"Batch insert failed, retry {retry_count}/{self.config.max_retries}: {e}"
                            )
                            await asyncio.sleep(self.config.retry_delay * retry_count)

                # Log progress
                if (i + len(batch)) % (self.config.batch_size * 10) == 0:
                    logger.debug(f"Processed {total_processed}/{len(records)} records")

        return {"processed": total_processed}

    async def _execute_batch_insert(
        self,
        session: AsyncSession,
        model_class: Any,
        batch: List[Dict[str, Any]],
        conflict_columns: List[str]
    ):
        """Execute batch insert for a specific model."""
        if self.config.conflict_action == "update":
            # Prepare update dictionary (exclude primary keys)
            update_dict = {
                col: insert(model_class).excluded[col]
                for col in batch[0].keys()
                if col not in conflict_columns
            }

            stmt = insert(model_class).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=conflict_columns,
                set_=update_dict
            )
        elif self.config.conflict_action == "ignore":
            stmt = insert(model_class).values(batch).on_conflict_do_nothing(
                index_elements=conflict_columns
            )
        else:
            stmt = insert(model_class).values(batch)

        await session.execute(stmt)

    async def _execute_generic_insert(
        self,
        session: AsyncSession,
        table_name: str,
        batch: List[Dict[str, Any]],
        conflict_columns: List[str]
    ):
        """Execute generic table insert using raw SQL."""
        if not batch:
            return

        # Build column names and values
        columns = list(batch[0].keys())
        placeholders = ", ".join([f":{col}" for col in columns])
        column_list = ", ".join(columns)

        # Build ON CONFLICT clause
        if self.config.conflict_action == "update":
            update_cols = [c for c in columns if c not in conflict_columns]
            update_clause = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols])
            conflict_clause = f"ON CONFLICT ({', '.join(conflict_columns)}) DO UPDATE SET {update_clause}"
        elif self.config.conflict_action == "ignore":
            conflict_clause = f"ON CONFLICT ({', '.join(conflict_columns)}) DO NOTHING"
        else:
            conflict_clause = ""

        # Build and execute query
        query = text(f"""
            INSERT INTO {table_name} ({column_list})
            VALUES ({placeholders})
            {conflict_clause}
        """)

        for record in batch:
            await session.execute(query, record)

    async def _bulk_copy_from(
        self,
        table_name: str,
        records: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        Use PostgreSQL COPY for ultra-fast bulk inserts.
        Note: This doesn't handle conflicts, suitable for initial loads.
        """
        if not records:
            return {"processed": 0}

        try:
            # Convert to DataFrame for easier CSV generation
            df = pd.DataFrame(records)

            # Create temporary CSV
            import tempfile
            import csv

            with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as tmp_file:
                df.to_csv(tmp_file, index=False, quoting=csv.QUOTE_MINIMAL)
                tmp_path = tmp_file.name

            async with self.db.session() as session:
                async with session.begin():
                    # Use raw connection for COPY command
                    conn = await session.connection()
                    raw_conn = await conn.get_raw_connection()

                    # Get column names
                    columns = ", ".join(df.columns)

                    # Execute COPY command
                    copy_sql = f"""
                        COPY {table_name} ({columns})
                        FROM STDIN WITH CSV HEADER
                    """

                    # Read file and execute COPY
                    with open(tmp_path, 'r') as f:
                        await raw_conn.driver_connection.copy_expert(copy_sql, f)

                    self._stats["total_inserted"] += len(records)

            # Clean up temp file
            import os
            os.unlink(tmp_path)

            logger.info(f"Successfully copied {len(records)} records using COPY")
            return {"processed": len(records)}

        except Exception as e:
            logger.error(f"COPY failed, falling back to batch insert: {e}")
            # Fallback to regular batch insert
            return await self._batch_insert_on_conflict(
                table_name, records, ["symbol", "trade_date"]
            )

    async def parallel_insert(
        self,
        table_name: str,
        records: List[Dict[str, Any]],
        conflict_columns: List[str],
        num_workers: int = 4
    ) -> Dict[str, int]:
        """
        Parallel insert using multiple workers for maximum throughput.

        Args:
            table_name: Target table name
            records: Records to insert
            conflict_columns: Columns defining uniqueness
            num_workers: Number of parallel workers

        Returns:
            Insert statistics
        """
        if not records:
            return self._stats

        logger.info(
            f"Starting parallel insert of {len(records)} records "
            f"into {table_name} using {num_workers} workers"
        )

        # Split records into chunks for workers
        chunk_size = len(records) // num_workers + 1
        chunks = [
            records[i:i + chunk_size]
            for i in range(0, len(records), chunk_size)
        ]

        # Create worker tasks
        tasks = []
        for i, chunk in enumerate(chunks):
            task = self._worker_insert(
                table_name, chunk, conflict_columns, worker_id=i
            )
            tasks.append(task)

        # Execute all workers in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate results
        total_processed = 0
        total_errors = 0

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Worker {i} failed: {result}")
                total_errors += len(chunks[i])
            else:
                total_processed += result.get("processed", 0)

        self._stats["total_errors"] += total_errors

        logger.info(
            f"Parallel insert completed: {total_processed} processed, "
            f"{total_errors} errors"
        )

        return {
            "processed": total_processed,
            "errors": total_errors
        }

    async def _worker_insert(
        self,
        table_name: str,
        records: List[Dict[str, Any]],
        conflict_columns: List[str],
        worker_id: int
    ) -> Dict[str, int]:
        """Worker function for parallel inserts."""
        logger.debug(f"Worker {worker_id} processing {len(records)} records")

        try:
            result = await self._batch_insert_on_conflict(
                table_name, records, conflict_columns
            )
            logger.debug(f"Worker {worker_id} completed")
            return result

        except Exception as e:
            logger.error(f"Worker {worker_id} failed: {e}")
            raise

    def get_statistics(self) -> Dict[str, Any]:
        """Get current insert statistics."""
        return self._stats.copy()

    def reset_statistics(self):
        """Reset statistics counters."""
        self._stats = {
            "total_inserted": 0,
            "total_updated": 0,
            "total_skipped": 0,
            "total_errors": 0,
            "last_batch_time": None,
            "avg_records_per_second": 0
        }


class OptimizedKlineSync:
    """Optimized K-line sync using batch insert service."""

    def __init__(
        self,
        db: DatabaseSessionManager,
        quote_client: Any,
        batch_config: Optional[BatchConfig] = None
    ):
        """Initialize optimized sync."""
        self.db = db
        self.quote_client = quote_client
        self.batch_service = BatchInsertService(db, batch_config)

    async def sync_daily_klines_batch(
        self,
        symbols: List[str],
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        parallel: bool = True
    ) -> Dict[str, Any]:
        """
        Sync daily K-lines using optimized batch processing.

        Args:
            symbols: List of symbols to sync
            start_date: Start date for sync
            end_date: End date for sync
            parallel: Use parallel processing

        Returns:
            Sync statistics
        """
        all_records = []

        # Fetch data for all symbols
        for symbol in symbols:
            try:
                logger.info(f"Fetching daily K-lines for {symbol}")

                # Get K-line data from API
                candles = await self.quote_client.get_history_candles(
                    symbol=symbol,
                    period="Day",
                    start=start_date,
                    end=end_date
                )

                # Convert to records
                for candle in candles:
                    record = {
                        "symbol": symbol,
                        "trade_date": candle.timestamp.date(),
                        "open": float(candle.open),
                        "high": float(candle.high),
                        "low": float(candle.low),
                        "close": float(candle.close),
                        "volume": int(candle.volume),
                        "turnover": float(candle.turnover) if candle.turnover else 0,
                        "num_trades": int(candle.trade_count) if hasattr(candle, 'trade_count') else 0
                    }
                    all_records.append(record)

            except Exception as e:
                logger.error(f"Error fetching data for {symbol}: {e}")
                continue

        if not all_records:
            logger.warning("No records to sync")
            return {"total": 0}

        logger.info(f"Syncing {len(all_records)} daily K-line records")

        # Use optimized batch insert
        if parallel and len(all_records) > 10000:
            result = await self.batch_service.parallel_insert(
                "kline_daily",
                all_records,
                ["symbol", "trade_date"],
                num_workers=4
            )
        else:
            result = await self.batch_service.bulk_insert_klines_optimized(
                "kline_daily",
                all_records,
                ["symbol", "trade_date"]
            )

        return result


__all__ = [
    "BatchConfig",
    "BatchInsertService",
    "OptimizedKlineSync"
]