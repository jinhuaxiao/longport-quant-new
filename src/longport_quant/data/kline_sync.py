"""K-line data synchronization service."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional

from loguru import logger
from longport import OpenApiException, openapi
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from longport_quant.config.settings import Settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.data.batch_insert import BatchConfig, BatchInsertService
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import KlineDaily, KlineMinute, SecurityStatic
from longport_quant.utils import ProgressTracker


class KlineDataService:
    """K-line data management and synchronization service."""

    def __init__(self, settings: Settings, db: DatabaseSessionManager, quote_client: QuoteDataClient):
        self.settings = settings
        self.db = db
        self.quote_client = quote_client
        # Initialize batch insert service for optimization
        self.batch_config = BatchConfig(
            batch_size=1000,
            chunk_size=10000,
            use_copy_from=True,
            conflict_action="update"
        )
        self.batch_service = BatchInsertService(db, self.batch_config)

    async def sync_daily_klines(
        self,
        symbols: List[str],
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> Dict[str, int]:
        """
        Sync daily K-line data for given symbols.

        Args:
            symbols: List of symbol codes
            start_date: Start date for sync (default: earliest available)
            end_date: End date for sync (default: today)

        Returns:
            Dictionary with symbol and record count
        """
        results: Dict[str, int] = {}
        sanitized_symbols = self._normalize_symbols(symbols)
        if not sanitized_symbols:
            logger.warning("No valid symbols provided for daily K-line sync")
            return results

        tracker = ProgressTracker(
            task_name="daily-kline-sync",
            total_steps=len(sanitized_symbols),
            unit_label="candles",
        )

        try:
            normalized_start, normalized_end = self._normalize_date_range(start_date, end_date)
        except ValueError as err:
            error_msg = f"Invalid date range for daily K-line sync: {err}"
            logger.error(error_msg)
            for symbol in sanitized_symbols:
                results[symbol] = -1
                tracker.record_failure(symbol, error=str(err))
            tracker.log_summary()
            return results

        for idx, symbol in enumerate(sanitized_symbols):
            try:
                last_date = await self._get_last_daily_sync_date(symbol)
                sync_start = normalized_start or last_date or date(2020, 1, 1)

                if sync_start >= normalized_end:
                    logger.info(f"Symbol {symbol} daily K-line is up to date")
                    results[symbol] = 0
                    tracker.record_success(symbol, message="up to date")
                    continue

                logger.info(
                    f"Syncing daily K-line for {symbol} from {sync_start} to {normalized_end}"
                )

                candles = await self.quote_client.get_history_candles(
                    symbol=symbol,
                    period=openapi.Period.Day,
                    adjust_type=openapi.AdjustType.ForwardAdjust,
                    start=datetime.combine(sync_start, datetime.min.time()),
                    end=datetime.combine(normalized_end, datetime.max.time())
                )

                # 每个股票间增加延迟，避免触发API限额
                if idx < len(sanitized_symbols) - 1:
                    await asyncio.sleep(0.5)

                if not isinstance(candles, list):
                    message = "Unexpected payload received for daily K-line sync"
                    logger.error(f"{message} ({symbol})")
                    results[symbol] = -1
                    tracker.record_failure(symbol, error=message)
                    continue

                if candles:
                    count = await self._bulk_upsert_daily_klines(symbol, candles)
                    results[symbol] = count
                    logger.info(f"Synced {count} daily K-line records for {symbol}")
                    tracker.record_success(symbol, processed_units=count)
                else:
                    results[symbol] = 0
                    tracker.record_success(symbol, message="no new records")

            except OpenApiException as api_err:
                error_code = str(api_err)

                # 检查是否是配额限制错误
                if "301607" in error_code or "out of limit" in error_code:
                    logger.error(
                        f"历史K线API配额已用尽，无法继续同步 {symbol}。"
                        f"建议：1) 等待明天配额重置 2) 使用 scripts/sync_realtime_fallback.py 同步今日数据"
                    )
                    results[symbol] = -1
                    tracker.record_failure(symbol, error="API配额用尽(301607)")

                    # 如果是配额错误，剩余的股票也会失败，直接中断
                    logger.warning(f"检测到配额限制，停止后续 {len(sanitized_symbols) - idx - 1} 个股票的同步")
                    for remaining_symbol in sanitized_symbols[idx + 1:]:
                        results[remaining_symbol] = -1
                        tracker.record_failure(remaining_symbol, error="跳过(配额已用尽)")
                    break
                else:
                    logger.exception(
                        f"LongPort API error syncing daily K-lines for {symbol}: {api_err}"
                    )
                    results[symbol] = -1
                    tracker.record_failure(symbol, error=str(api_err))
            except SQLAlchemyError as db_err:
                logger.exception(
                    f"Database error syncing daily K-lines for {symbol}: {db_err}"
                )
                results[symbol] = -1
                tracker.record_failure(symbol, error=str(db_err))
            except Exception as err:
                logger.exception(
                    f"Unexpected error syncing daily K-lines for {symbol}: {err}"
                )
                results[symbol] = -1
                tracker.record_failure(symbol, error=str(err))

        tracker.log_summary()
        return results

    async def sync_minute_klines(
        self,
        symbols: List[str],
        days_back: int = 180
    ) -> Dict[str, int]:
        """
        Sync minute K-line data for given symbols (keeping only recent data).

        Args:
            symbols: List of symbol codes
            days_back: Number of days to keep (default: 180 days / 6 months)

        Returns:
            Dictionary with symbol and record count
        """
        results: Dict[str, int] = {}
        sanitized_symbols = self._normalize_symbols(symbols)
        if not sanitized_symbols:
            logger.warning("No valid symbols provided for minute K-line sync")
            return results

        tracker = ProgressTracker(
            task_name="minute-kline-sync",
            total_steps=len(sanitized_symbols),
            unit_label="candles",
        )

        if days_back <= 0:
            message = "days_back must be positive for minute K-line sync"
            logger.error(message)
            for symbol in sanitized_symbols:
                results[symbol] = -1
                tracker.record_failure(symbol, error=message)
            tracker.log_summary()
            return results

        cutoff_date = datetime.now() - timedelta(days=days_back)

        for symbol in sanitized_symbols:
            max_retries = 3
            retry_count = 0
            retry_delay = 2  # 初始延迟2秒

            while retry_count <= max_retries:
                try:
                    sync_end = datetime.now()
                    logger.info(f"Syncing minute K-line for {symbol} from {cutoff_date} to {sync_end}")

                    candles = await self.quote_client.get_history_candles(
                        symbol=symbol,
                        period=openapi.Period.Min_1,
                        adjust_type=openapi.AdjustType.NoAdjust,
                        start=cutoff_date,
                        end=sync_end
                    )

                    if not isinstance(candles, list):
                        message = "Unexpected payload received for minute K-line sync"
                        logger.error(f"{message} ({symbol})")
                        results[symbol] = -1
                        tracker.record_failure(symbol, error=message)
                        break

                    if candles:
                        count = await self._bulk_upsert_minute_klines(symbol, candles)
                        results[symbol] = count
                        logger.info(f"Synced {count} minute K-line records for {symbol}")
                        tracker.record_success(symbol, processed_units=count)
                    else:
                        results[symbol] = 0
                        tracker.record_success(symbol, message="no new records")

                    # 成功后添加延迟，避免频繁请求
                    await asyncio.sleep(0.5)
                    break  # 成功则跳出重试循环

                except OpenApiException as api_err:
                    error_code = str(api_err)

                    # 检查是否是限流错误 (301607)
                    if "301607" in error_code or "out of limit" in error_code:
                        if retry_count < max_retries:
                            retry_count += 1
                            wait_time = retry_delay * (2 ** (retry_count - 1))  # 指数退避
                            logger.warning(
                                f"API limit reached for {symbol}, retrying in {wait_time}s "
                                f"(attempt {retry_count}/{max_retries})"
                            )
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            logger.error(
                                f"Max retries reached for {symbol} after API limit errors: {api_err}"
                            )
                            results[symbol] = -1
                            tracker.record_failure(symbol, error=f"API limit (max retries): {api_err}")
                            break
                    else:
                        # 其他API错误，不重试
                        logger.exception(
                            f"LongPort API error syncing minute K-lines for {symbol}: {api_err}"
                        )
                        results[symbol] = -1
                        tracker.record_failure(symbol, error=str(api_err))
                        break

                except SQLAlchemyError as db_err:
                    logger.exception(
                        f"Database error syncing minute K-lines for {symbol}: {db_err}"
                    )
                    results[symbol] = -1
                    tracker.record_failure(symbol, error=str(db_err))
                    break

                except Exception as err:
                    logger.exception(
                        f"Unexpected error syncing minute K-lines for {symbol}: {err}"
                    )
                    results[symbol] = -1
                tracker.record_failure(symbol, error=str(err))

        tracker.log_summary()
        return results

    async def cleanup_old_minute_data(self, days_to_keep: int = 180) -> int:
        """
        Clean up old minute K-line data.

        Args:
            days_to_keep: Number of days to keep

        Returns:
            Number of records deleted
        """
        if days_to_keep <= 0:
            logger.warning("days_to_keep must be positive for minute data cleanup; skipping")
            return 0

        cutoff_date = datetime.now() - timedelta(days=days_to_keep)
        deleted_count = 0

        async with self.db.session() as session:
            try:
                async with session.begin():
                    result = await session.execute(
                        text("DELETE FROM kline_minute WHERE timestamp < :cutoff"),
                        {"cutoff": cutoff_date}
                    )
                    deleted_count = result.rowcount or 0
            except SQLAlchemyError as db_err:
                logger.exception(
                    f"Database error while cleaning minute K-line data older than {cutoff_date}: {db_err}"
                )
                raise

        logger.info(f"Deleted {deleted_count} old minute K-line records before {cutoff_date}")
        return deleted_count

    async def sync_security_static(self, symbols: List[str]) -> int:
        """
        Sync security static information.

        Args:
            symbols: List of symbol codes

        Returns:
            Number of records updated
        """
        sanitized_symbols = self._normalize_symbols(symbols)
        if not sanitized_symbols:
            logger.warning("No valid symbols provided for security static sync")
            return 0

        tracker = ProgressTracker(
            task_name="security-static-sync",
            total_steps=len(sanitized_symbols),
            unit_label="records",
        )

        try:
            static_infos = await self.quote_client.get_static_info(sanitized_symbols)
        except OpenApiException as api_err:
            logger.exception(f"LongPort API error fetching security static data: {api_err}")
            for symbol in sanitized_symbols:
                tracker.record_failure(symbol, error=str(api_err))
            tracker.log_summary()
            return 0
        except Exception as err:
            logger.exception(f"Unexpected error fetching security static data: {err}")
            for symbol in sanitized_symbols:
                tracker.record_failure(symbol, error=str(err))
            tracker.log_summary()
            return 0

        if not static_infos:
            logger.info("Security static API returned no records to process")
            for symbol in sanitized_symbols:
                tracker.record_success(symbol, message="no data returned")
            tracker.log_summary()
            return 0

        info_by_symbol: Dict[str, object] = {}
        for info in static_infos:
            symbol = getattr(info, "symbol", None)
            if not symbol:
                logger.debug("Skipping static info payload missing symbol")
                continue
            info_by_symbol.setdefault(symbol, info)

        updated_count = 0
        skipped_count = 0

        async with self.db.session() as session:
            for symbol in sanitized_symbols:
                info = info_by_symbol.get(symbol)
                if not info:
                    tracker.record_failure(symbol, error="no static info returned")
                    continue

                try:
                    insert_values, update_values = self._prepare_static_upsert(info)
                except ValueError as validation_err:
                    skipped_count += 1
                    logger.debug(
                        "Skipping static info record for {} due to validation error: {}",
                        symbol,
                        validation_err,
                    )
                    tracker.record_failure(symbol, error=str(validation_err))
                    continue

                stmt = insert(SecurityStatic).values(**insert_values).on_conflict_do_update(
                    index_elements=["symbol"],
                    set_=update_values,
                )

                try:
                    async with session.begin():
                        await session.execute(stmt)
                except SQLAlchemyError as db_err:
                    logger.exception(
                        "Database error while syncing security static data for {}: {}",
                        symbol,
                        db_err,
                    )
                    tracker.record_failure(symbol, error=str(db_err))
                    continue
                except Exception as err:
                    logger.exception(
                        "Unexpected error while syncing security static data for {}: {}",
                        symbol,
                        err,
                    )
                    tracker.record_failure(symbol, error=str(err))
                    continue

                updated_count += 1
                tracker.record_success(symbol, processed_units=1)

        if skipped_count:
            logger.warning(f"Skipped {skipped_count} invalid security static records")

        logger.info(f"Updated {updated_count} security static records")
        tracker.log_summary()
        return updated_count

    async def _get_last_daily_sync_date(self, symbol: str) -> Optional[date]:
        """Get the last synced date for daily K-line."""
        async with self.db.session() as session:
            stmt = select(KlineDaily.trade_date).where(
                KlineDaily.symbol == symbol
            ).order_by(KlineDaily.trade_date.desc()).limit(1)

            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return row if row else None

    async def _bulk_upsert_daily_klines(self, symbol: str, candles: List[openapi.Candlestick]) -> int:
        """Bulk insert or update daily K-line data."""
        processed = 0
        skipped = 0
        batch: List[Dict[str, object]] = []
        update_columns: Optional[List[str]] = None
        batch_size = max(1, self.batch_config.batch_size)

        async with self.db.session() as session:
            try:
                async with session.begin():
                    for candle in candles:
                        try:
                            insert_values, update_values = self._prepare_daily_upsert(symbol, candle)
                        except ValueError as validation_err:
                            skipped += 1
                            logger.debug(
                                "Skipping invalid daily candle for {}: {}",
                                symbol,
                                validation_err,
                            )
                            continue

                        batch.append(insert_values)
                        if update_columns is None:
                            update_columns = list(update_values.keys())

                        if len(batch) >= batch_size:
                            processed += await self._execute_upsert_batch(
                                session,
                                KlineDaily,
                                batch,
                                ["symbol", "trade_date"],
                                update_columns or [],
                            )
                            batch.clear()

                    if batch:
                        processed += await self._execute_upsert_batch(
                            session,
                            KlineDaily,
                            batch,
                            ["symbol", "trade_date"],
                            update_columns or [],
                        )
                        batch.clear()
            except SQLAlchemyError as db_err:
                logger.exception(
                    f"Database error during daily K-line upsert for {symbol}: {db_err}"
                )
                raise
            except Exception as err:
                logger.exception(
                    f"Unexpected error during daily K-line upsert for {symbol}: {err}"
                )
                raise

        if skipped:
            logger.warning(f"Skipped {skipped} invalid daily K-line candles for {symbol}")

        return processed

    async def _bulk_upsert_minute_klines(self, symbol: str, candles: List[openapi.Candlestick]) -> int:
        """Bulk insert or update minute K-line data."""
        processed = 0
        skipped = 0
        batch: List[Dict[str, object]] = []
        update_columns: Optional[List[str]] = None
        batch_size = max(1, self.batch_config.batch_size)

        async with self.db.session() as session:
            try:
                async with session.begin():
                    for candle in candles:
                        try:
                            insert_values, update_values = self._prepare_minute_upsert(symbol, candle)
                        except ValueError as validation_err:
                            skipped += 1
                            logger.debug(
                                "Skipping invalid minute candle for {}: {}",
                                symbol,
                                validation_err,
                            )
                            continue

                        batch.append(insert_values)
                        if update_columns is None:
                            update_columns = list(update_values.keys())

                        if len(batch) >= batch_size:
                            processed += await self._execute_upsert_batch(
                                session,
                                KlineMinute,
                                batch,
                                ["symbol", "timestamp"],
                                update_columns or [],
                            )
                            batch.clear()

                    if batch:
                        processed += await self._execute_upsert_batch(
                            session,
                            KlineMinute,
                            batch,
                            ["symbol", "timestamp"],
                            update_columns or [],
                        )
                        batch.clear()
            except SQLAlchemyError as db_err:
                logger.exception(
                    f"Database error during minute K-line upsert for {symbol}: {db_err}"
                )
                raise
            except Exception as err:
                logger.exception(
                    f"Unexpected error during minute K-line upsert for {symbol}: {err}"
                )
                raise

        if skipped:
            logger.warning(f"Skipped {skipped} invalid minute K-line candles for {symbol}")

        return processed

    async def _execute_upsert_batch(
        self,
        session: AsyncSession,
        model: type,
        batch: List[Dict[str, object]],
        conflict_columns: List[str],
        update_columns: List[str],
    ) -> int:
        """Execute a batched upsert statement for the provided model."""
        if not batch:
            return 0

        stmt = insert(model).values(batch)

        if update_columns:
            update_mapping = {column: getattr(stmt.excluded, column) for column in update_columns}
            stmt = stmt.on_conflict_do_update(
                index_elements=conflict_columns,
                set_=update_mapping,
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=conflict_columns)

        await session.execute(stmt)
        return len(batch)

    @staticmethod
    def _normalize_symbols(symbols: List[str]) -> List[str]:
        """Strip, filter, and deduplicate symbol inputs while preserving order."""
        if not symbols:
            return []

        seen: set[str] = set()
        normalized: List[str] = []
        for raw_symbol in symbols:
            if not raw_symbol:
                continue
            symbol = raw_symbol.strip()
            if not symbol or symbol in seen:
                continue
            normalized.append(symbol)
            seen.add(symbol)
        return normalized

    @staticmethod
    def _normalize_date_range(
        start_date: Optional[date],
        end_date: Optional[date],
    ) -> tuple[Optional[date], date]:
        """Validate and normalise the requested date range."""
        resolved_end = end_date or date.today()
        if start_date and start_date > resolved_end:
            raise ValueError("start_date cannot be after end_date")
        return start_date, resolved_end

    @staticmethod
    def _to_decimal(value: object, allow_none: bool = False) -> Optional[Decimal]:
        """Convert a numeric value to Decimal with optional None handling."""
        if value is None:
            if allow_none:
                return None
            raise ValueError("Value is required")

        if isinstance(value, Decimal):
            decimal_value = value
        else:
            try:
                decimal_value = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError) as err:
                if allow_none:
                    return None
                raise ValueError(f"Unable to convert value to Decimal: {value}") from err

        if decimal_value.is_nan():
            if allow_none:
                return None
            raise ValueError("Numeric value is NaN")

        return decimal_value

    @staticmethod
    def _to_int(value: object, field: str) -> int:
        """Convert a value to int, raising a descriptive error on failure."""
        if value is None:
            raise ValueError(f"{field} is required")
        try:
            return int(value)
        except (TypeError, ValueError) as err:
            raise ValueError(f"Invalid {field} value: {value}") from err

    def _prepare_daily_upsert(
        self, symbol: str, candle: openapi.Candlestick
    ) -> tuple[Dict[str, object], Dict[str, object]]:
        """Prepare insert and update payloads for a daily candle."""
        timestamp = getattr(candle, "timestamp", None)
        if not isinstance(timestamp, datetime):
            raise ValueError("Daily candle is missing a valid timestamp")

        open_price = self._to_decimal(getattr(candle, "open", None))
        high_price = self._to_decimal(getattr(candle, "high", None))
        low_price = self._to_decimal(getattr(candle, "low", None))
        close_price = self._to_decimal(getattr(candle, "close", None))
        volume = self._to_int(getattr(candle, "volume", None), "volume")
        turnover = self._to_decimal(getattr(candle, "turnover", None), allow_none=True)
        raw_change_val = self._to_decimal(getattr(candle, "change_val", None), allow_none=True)

        prev_close = close_price - raw_change_val if raw_change_val is not None else close_price
        change_amount = close_price - prev_close

        change_rate = Decimal("0")
        amplitude = Decimal("0")
        if prev_close > Decimal("0"):
            try:
                change_rate = (change_amount / prev_close) * Decimal("100")
                amplitude = (high_price - low_price) / prev_close * Decimal("100")
            except (InvalidOperation, ZeroDivisionError):
                change_rate = Decimal("0")
                amplitude = Decimal("0")

        now = datetime.utcnow()
        trade_date = timestamp.date()

        insert_values: Dict[str, object] = {
            "symbol": symbol,
            "trade_date": trade_date,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
            "turnover": turnover,
            "prev_close": prev_close,
            "change_val": change_amount,
            "change_rate": change_rate,
            "amplitude": amplitude,
            "turnover_rate": None,
            "adjust_flag": 1,
            "created_at": now,
            "updated_at": now,
        }

        update_values: Dict[str, object] = {
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
            "turnover": turnover,
            "prev_close": prev_close,
            "change_val": change_amount,
            "change_rate": change_rate,
            "amplitude": amplitude,
            "updated_at": now,
        }

        return insert_values, update_values

    def _prepare_minute_upsert(
        self, symbol: str, candle: openapi.Candlestick
    ) -> tuple[Dict[str, object], Dict[str, object]]:
        """Prepare insert and update payloads for a minute candle."""
        timestamp = getattr(candle, "timestamp", None)
        if not isinstance(timestamp, datetime):
            raise ValueError("Minute candle is missing a valid timestamp")

        open_price = self._to_decimal(getattr(candle, "open", None))
        high_price = self._to_decimal(getattr(candle, "high", None))
        low_price = self._to_decimal(getattr(candle, "low", None))
        close_price = self._to_decimal(getattr(candle, "close", None))
        volume = self._to_int(getattr(candle, "volume", None), "volume")
        turnover = self._to_decimal(getattr(candle, "turnover", None), allow_none=True)

        raw_trade_count = getattr(candle, "trade_count", None)
        if raw_trade_count is None:
            raw_trade_count = getattr(candle, "trade_num", None)
        trade_count = (
            self._to_int(raw_trade_count, "trade_count") if raw_trade_count is not None else None
        )

        now = datetime.utcnow()

        insert_values: Dict[str, object] = {
            "symbol": symbol,
            "timestamp": timestamp,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
            "turnover": turnover,
            "trade_count": trade_count,
            "created_at": now,
        }

        update_values: Dict[str, object] = {
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": volume,
            "turnover": turnover,
            "trade_count": trade_count,
        }

        return insert_values, update_values

    def _prepare_static_upsert(self, info: object) -> tuple[Dict[str, object], Dict[str, object]]:
        """Prepare insert and update payloads for security static information."""
        symbol = getattr(info, "symbol", None)
        if not symbol:
            raise ValueError("Security static record is missing symbol")

        lot_size_raw = getattr(info, "lot_size", None)
        total_shares_raw = getattr(info, "total_shares", None)
        circulating_shares_raw = getattr(info, "circulating_shares", None)

        lot_size = self._to_int(lot_size_raw, "lot_size") if lot_size_raw is not None else None
        total_shares = (
            self._to_int(total_shares_raw, "total_shares") if total_shares_raw is not None else None
        )
        circulating_shares = (
            self._to_int(circulating_shares_raw, "circulating_shares")
            if circulating_shares_raw is not None
            else None
        )

        eps = self._to_decimal(getattr(info, "eps", None), allow_none=True)
        eps_ttm = self._to_decimal(getattr(info, "eps_ttm", None), allow_none=True)
        bps = self._to_decimal(getattr(info, "bps", None), allow_none=True)
        dividend_yield = self._to_decimal(
            getattr(info, "dividend_yield", None), allow_none=True
        )

        now = datetime.utcnow()

        insert_values: Dict[str, object] = {
            "symbol": symbol,
            "name_cn": getattr(info, "name_cn", None),
            "name_en": getattr(info, "name_en", None),
            "exchange": getattr(info, "exchange", None),
            "currency": getattr(info, "currency", None),
            "lot_size": lot_size,
            "total_shares": total_shares,
            "circulating_shares": circulating_shares,
            "eps": eps,
            "eps_ttm": eps_ttm,
            "bps": bps,
            "dividend_yield": dividend_yield,
            "board": str(getattr(info, "board", None)) if getattr(info, "board", None) is not None else None,
            "updated_at": now,
        }

        update_values = insert_values.copy()
        update_values.pop("symbol", None)

        return insert_values, update_values

    async def sync_daily_klines_optimized(
        self,
        symbols: List[str],
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        use_parallel: bool = True
    ) -> Dict[str, Any]:
        """
        Optimized daily K-line sync using batch processing.

        Args:
            symbols: List of symbol codes
            start_date: Start date for sync
            end_date: End date for sync
            use_parallel: Use parallel processing for large datasets

        Returns:
            Sync statistics
        """
        sanitized_symbols = self._normalize_symbols(symbols)
        if not sanitized_symbols:
            logger.warning("No valid symbols for optimized sync")
            return {"total": 0, "errors": 0}

        normalized_start, normalized_end = self._normalize_date_range(start_date, end_date)
        all_records = []
        failed_symbols = []

        # Collect all records first
        for symbol in sanitized_symbols:
            try:
                last_date = await self._get_last_daily_sync_date(symbol)
                sync_start = normalized_start or last_date or date(2020, 1, 1)

                if sync_start >= normalized_end:
                    logger.info(f"{symbol} already up to date")
                    continue

                logger.info(f"Fetching {symbol} from {sync_start} to {normalized_end}")

                candles = await self.quote_client.get_history_candles(
                    symbol=symbol,
                    period=openapi.Period.Day,
                    adjust_type=openapi.AdjustType.Forward,
                    start=datetime.combine(sync_start, datetime.min.time()),
                    end=datetime.combine(normalized_end, datetime.max.time())
                )

                # Convert to batch records
                for candle in candles:
                    try:
                        insert_values, _ = self._prepare_daily_upsert(symbol, candle)
                        all_records.append(insert_values)
                    except ValueError as e:
                        logger.debug(f"Skipping invalid candle: {e}")
                        continue

            except Exception as e:
                logger.error(f"Failed to fetch {symbol}: {e}")
                failed_symbols.append(symbol)

        if not all_records:
            logger.info("No records to sync")
            return {
                "total": 0,
                "symbols_processed": len(sanitized_symbols) - len(failed_symbols),
                "symbols_failed": len(failed_symbols)
            }

        # Use optimized batch insert
        logger.info(f"Batch inserting {len(all_records)} daily K-line records")

        if use_parallel and len(all_records) > 10000:
            # Use parallel processing for large datasets
            result = await self.batch_service.parallel_insert(
                "kline_daily",
                all_records,
                ["symbol", "trade_date"],
                num_workers=4
            )
        else:
            # Use standard batch insert
            result = await self.batch_service.bulk_insert_klines_optimized(
                "kline_daily",
                all_records,
                ["symbol", "trade_date"]
            )

        # Add symbol statistics
        result["symbols_processed"] = len(sanitized_symbols) - len(failed_symbols)
        result["symbols_failed"] = len(failed_symbols)
        result["failed_symbols"] = failed_symbols

        return result

    async def sync_minute_klines_optimized(
        self,
        symbols: List[str],
        interval: int = 1,
        days_back: int = 30,
        use_parallel: bool = True
    ) -> Dict[str, Any]:
        """
        Optimized minute K-line sync using batch processing.

        Args:
            symbols: List of symbol codes
            interval: Minute interval (1, 5, 15, 30, 60)
            days_back: Number of days to sync back
            use_parallel: Use parallel processing

        Returns:
            Sync statistics
        """
        sanitized_symbols = self._normalize_symbols(symbols)
        if not sanitized_symbols:
            return {"total": 0, "errors": 0}

        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)
        all_records = []
        failed_symbols = []

        # Map interval to period
        period_map = {
            1: openapi.Period.Min_1,
            5: openapi.Period.Min_5,
            15: openapi.Period.Min_15,
            30: openapi.Period.Min_30,
            60: openapi.Period.Min_60
        }
        period = period_map.get(interval, openapi.Period.Min_1)

        # Collect all records
        for symbol in sanitized_symbols:
            try:
                logger.info(f"Fetching {interval}min K-lines for {symbol}")

                candles = await self.quote_client.get_history_candles(
                    symbol=symbol,
                    period=period,
                    adjust_type=openapi.AdjustType.Forward,
                    start=start_date,
                    end=end_date
                )

                # Convert to batch records
                for candle in candles:
                    try:
                        insert_values, _ = self._prepare_minute_upsert(symbol, candle)
                        insert_values["interval"] = interval
                        all_records.append(insert_values)
                    except ValueError as e:
                        logger.debug(f"Skipping invalid candle: {e}")
                        continue

            except Exception as e:
                logger.error(f"Failed to fetch {symbol}: {e}")
                failed_symbols.append(symbol)

        if not all_records:
            return {
                "total": 0,
                "symbols_processed": len(sanitized_symbols) - len(failed_symbols),
                "symbols_failed": len(failed_symbols)
            }

        # Use optimized batch insert
        logger.info(f"Batch inserting {len(all_records)} minute K-line records")

        if use_parallel and len(all_records) > 50000:
            result = await self.batch_service.parallel_insert(
                "kline_minute",
                all_records,
                ["symbol", "timestamp"],
                num_workers=4
            )
        else:
            result = await self.batch_service.bulk_insert_klines_optimized(
                "kline_minute",
                all_records,
                ["symbol", "timestamp"]
            )

        result["symbols_processed"] = len(sanitized_symbols) - len(failed_symbols)
        result["symbols_failed"] = len(failed_symbols)
        result["failed_symbols"] = failed_symbols

        return result
