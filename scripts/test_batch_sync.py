#!/usr/bin/env python3
"""Test script for optimized batch synchronization."""

import asyncio
import sys
import os
from datetime import datetime, date, timedelta

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from longport_quant.config.settings import get_settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.data.kline_sync import KlineDataService
from longport_quant.data.batch_insert import BatchConfig


async def test_batch_sync():
    """Test the optimized batch sync functionality."""

    # Setup logging
    logger.add(
        f"batch_sync_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
        rotation="100 MB",
        level="DEBUG"
    )

    # Initialize components
    settings = get_settings()
    db = DatabaseSessionManager(settings.database_dsn)
    quote_client = QuoteDataClient(settings)

    # Create kline service
    kline_service = KlineDataService(settings, db, quote_client)

    # Test symbols
    test_symbols = ["700.HK", "9988.HK", "3690.HK"]  # Tencent, Alibaba, Meituan

    print("\n" + "="*60)
    print("BATCH SYNC PERFORMANCE TEST")
    print("="*60)

    try:
        # Test 1: Standard sync (baseline)
        print("\n1. Testing standard sync (baseline)...")
        start_time = datetime.now()

        result_standard = await kline_service.sync_daily_klines(
            symbols=test_symbols[:1],  # Just one symbol
            start_date=date.today() - timedelta(days=30),
            end_date=date.today()
        )

        standard_time = (datetime.now() - start_time).total_seconds()
        print(f"   Standard sync completed in {standard_time:.2f} seconds")
        print(f"   Results: {result_standard}")

        # Test 2: Optimized batch sync
        print("\n2. Testing optimized batch sync...")
        start_time = datetime.now()

        result_optimized = await kline_service.sync_daily_klines_optimized(
            symbols=test_symbols,
            start_date=date.today() - timedelta(days=30),
            end_date=date.today(),
            use_parallel=False
        )

        optimized_time = (datetime.now() - start_time).total_seconds()
        print(f"   Optimized sync completed in {optimized_time:.2f} seconds")
        print(f"   Results: {result_optimized}")

        # Test 3: Parallel batch sync
        print("\n3. Testing parallel batch sync...")
        start_time = datetime.now()

        result_parallel = await kline_service.sync_daily_klines_optimized(
            symbols=test_symbols,
            start_date=date.today() - timedelta(days=90),
            end_date=date.today(),
            use_parallel=True
        )

        parallel_time = (datetime.now() - start_time).total_seconds()
        print(f"   Parallel sync completed in {parallel_time:.2f} seconds")
        print(f"   Results: {result_parallel}")

        # Test 4: Minute K-line sync
        print("\n4. Testing minute K-line batch sync...")
        start_time = datetime.now()

        result_minute = await kline_service.sync_minute_klines_optimized(
            symbols=test_symbols[:2],
            interval=5,  # 5-minute K-lines
            days_back=7,
            use_parallel=True
        )

        minute_time = (datetime.now() - start_time).total_seconds()
        print(f"   Minute K-line sync completed in {minute_time:.2f} seconds")
        print(f"   Results: {result_minute}")

        # Print performance summary
        print("\n" + "="*60)
        print("PERFORMANCE SUMMARY")
        print("="*60)

        if standard_time > 0:
            speedup_batch = standard_time / optimized_time if optimized_time > 0 else 0
            speedup_parallel = standard_time / parallel_time if parallel_time > 0 else 0

            print(f"Standard sync:    {standard_time:.2f}s (baseline)")
            print(f"Optimized sync:   {optimized_time:.2f}s ({speedup_batch:.1f}x faster)")
            print(f"Parallel sync:    {parallel_time:.2f}s ({speedup_parallel:.1f}x faster)")
            print(f"Minute K-line:    {minute_time:.2f}s")

        # Test batch configuration options
        print("\n" + "="*60)
        print("TESTING BATCH CONFIGURATIONS")
        print("="*60)

        # Test with different batch sizes
        for batch_size in [500, 1000, 2000]:
            kline_service.batch_config.batch_size = batch_size

            print(f"\nTesting with batch_size={batch_size}...")
            start_time = datetime.now()

            result = await kline_service.sync_daily_klines_optimized(
                symbols=test_symbols[:1],
                start_date=date.today() - timedelta(days=60),
                end_date=date.today(),
                use_parallel=False
            )

            elapsed = (datetime.now() - start_time).total_seconds()
            stats = kline_service.batch_service.get_statistics()

            print(f"   Time: {elapsed:.2f}s")
            print(f"   Records/sec: {stats.get('avg_records_per_second', 0):.0f}")

        # Test COPY strategy (if available)
        print("\n" + "="*60)
        print("TESTING COPY STRATEGY")
        print("="*60)

        kline_service.batch_config.use_copy_from = True
        kline_service.batch_config.batch_size = 5000

        print("Testing PostgreSQL COPY for bulk insert...")
        start_time = datetime.now()

        # Need enough records to trigger COPY
        result_copy = await kline_service.sync_daily_klines_optimized(
            symbols=test_symbols,
            start_date=date.today() - timedelta(days=365),
            end_date=date.today(),
            use_parallel=False
        )

        copy_time = (datetime.now() - start_time).total_seconds()
        print(f"   COPY completed in {copy_time:.2f} seconds")
        print(f"   Results: {result_copy}")

        # Final statistics
        print("\n" + "="*60)
        print("FINAL STATISTICS")
        print("="*60)

        final_stats = kline_service.batch_service.get_statistics()
        print(f"Total inserted:    {final_stats['total_inserted']}")
        print(f"Total updated:     {final_stats['total_updated']}")
        print(f"Total errors:      {final_stats['total_errors']}")
        print(f"Avg speed:         {final_stats['avg_records_per_second']:.0f} records/sec")

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        raise

    finally:
        await db.close()
        print("\nTest completed!")


async def verify_data_integrity():
    """Verify that synced data is correct."""
    settings = get_settings()
    db = DatabaseSessionManager(settings.database_dsn)

    try:
        async with db.session() as session:
            # Check daily K-line count
            from sqlalchemy import select, func
            from longport_quant.persistence.models import KlineDaily, KlineMinute

            # Count daily K-lines
            daily_count = await session.execute(
                select(func.count()).select_from(KlineDaily)
            )
            daily_total = daily_count.scalar()

            # Count minute K-lines
            minute_count = await session.execute(
                select(func.count()).select_from(KlineMinute)
            )
            minute_total = minute_count.scalar()

            # Get sample records
            daily_sample = await session.execute(
                select(KlineDaily).limit(5)
            )
            daily_records = daily_sample.scalars().all()

            print("\n" + "="*60)
            print("DATA INTEGRITY CHECK")
            print("="*60)
            print(f"Total daily K-lines:  {daily_total}")
            print(f"Total minute K-lines: {minute_total}")

            if daily_records:
                print("\nSample daily K-line records:")
                for record in daily_records[:3]:
                    print(f"  {record.symbol} {record.trade_date}: "
                          f"O:{record.open} H:{record.high} "
                          f"L:{record.low} C:{record.close}")

    finally:
        await db.close()


if __name__ == "__main__":
    print("Starting batch sync test...")

    # Run main test
    asyncio.run(test_batch_sync())

    # Verify data
    asyncio.run(verify_data_integrity())