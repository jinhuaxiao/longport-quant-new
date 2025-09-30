#!/usr/bin/env python3
"""Real-time data synchronization script."""

import asyncio
import argparse
import signal
from datetime import datetime, timedelta
from typing import List, Set, Dict, Any
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from loguru import logger
from longport import openapi
from longport_quant.config.settings import Settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import (
    RealtimeQuote, MarketDepth, CalcIndicator, KlineMinute
)
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.data.watchlist import WatchlistLoader
from sqlalchemy.dialects.postgresql import insert
from decimal import Decimal


class RealtimeDataSync:
    """Real-time data synchronization service."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = DatabaseSessionManager(settings.database_dsn)
        self.quote_client = QuoteDataClient(settings)
        self.quote_ctx: openapi.QuoteContext | None = None
        self.subscribed_symbols: Set[str] = set()
        self.running = False
        self._tasks: List[asyncio.Task] = []

    async def start(self, symbols: List[str]):
        """Start real-time data synchronization."""
        logger.info(f"Starting real-time sync for {len(symbols)} symbols")

        async with self.db as database:
            self.database = database

            # Initialize quote context for subscription
            config = openapi.Config.from_env()
            self.quote_ctx = openapi.QuoteContext(config)

            try:
                # Subscribe to real-time data
                await self._subscribe_realtime(symbols)

                # Start background tasks
                self.running = True
                self._tasks = [
                    asyncio.create_task(self._process_realtime_quotes()),
                    asyncio.create_task(self._sync_market_depth_periodically(symbols)),
                    asyncio.create_task(self._sync_calc_indicators_periodically(symbols)),
                    asyncio.create_task(self._sync_minute_klines_periodically(symbols))
                ]

                # Wait for tasks
                await asyncio.gather(*self._tasks)

            except asyncio.CancelledError:
                logger.info("Real-time sync cancelled")
            except Exception as e:
                logger.error(f"Real-time sync error: {e}")
                raise
            finally:
                await self.stop()

    async def stop(self):
        """Stop real-time data synchronization."""
        logger.info("Stopping real-time sync...")
        self.running = False

        # Cancel all tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

        # Wait for tasks to complete
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        # Unsubscribe and cleanup
        if self.quote_ctx:
            try:
                if self.subscribed_symbols:
                    self.quote_ctx.unsubscribe(
                        list(self.subscribed_symbols),
                        [openapi.SubType.Quote, openapi.SubType.Depth]
                    )
            except:
                pass
            self.quote_ctx = None

        logger.info("Real-time sync stopped")

    async def _subscribe_realtime(self, symbols: List[str]):
        """Subscribe to real-time quote and depth updates."""
        if not self.quote_ctx:
            return

        # Subscribe in batches (max 500 per request)
        batch_size = 500
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]

            try:
                # Subscribe to quote and depth
                self.quote_ctx.subscribe(
                    batch,
                    [openapi.SubType.Quote, openapi.SubType.Depth],
                    is_first_push=True  # Get initial data immediately
                )
                self.subscribed_symbols.update(batch)
                logger.info(f"Subscribed to {len(batch)} symbols")

            except Exception as e:
                logger.error(f"Failed to subscribe batch: {e}")

    async def _process_realtime_quotes(self):
        """Process real-time quote updates."""
        if not self.quote_ctx:
            return

        # Get event loop for scheduling coroutines from callback
        loop = asyncio.get_event_loop()

        def on_quote(_, event: openapi.PushQuote):
            """Handle quote push event."""
            # Schedule coroutine in event loop from sync callback
            asyncio.run_coroutine_threadsafe(
                self._save_realtime_quote(event),
                loop
            )

        # Set callback
        self.quote_ctx.set_on_quote(on_quote)

        # Keep running
        while self.running:
            await asyncio.sleep(1)

    async def _save_realtime_quote(self, quote: openapi.PushQuote):
        """Save real-time quote to database."""
        try:
            async with self.database.session() as session:
                stmt = insert(RealtimeQuote).values(
                    symbol=quote.symbol,
                    timestamp=quote.timestamp or datetime.now(),
                    last_done=Decimal(str(quote.last_done)) if quote.last_done else None,
                    prev_close=Decimal(str(quote.prev_close)) if quote.prev_close else None,
                    open=Decimal(str(quote.open)) if quote.open else None,
                    high=Decimal(str(quote.high)) if quote.high else None,
                    low=Decimal(str(quote.low)) if quote.low else None,
                    volume=quote.volume,
                    turnover=Decimal(str(quote.turnover)) if quote.turnover else None,
                    bid_price=None,  # Will be updated from depth
                    ask_price=None,
                    bid_volume=None,
                    ask_volume=None,
                    trade_status=quote.trade_status if hasattr(quote, 'trade_status') else None
                ).on_conflict_do_update(
                    index_elements=['symbol', 'timestamp'],
                    set_=dict(
                        last_done=Decimal(str(quote.last_done)) if quote.last_done else None,
                        high=Decimal(str(quote.high)) if quote.high else None,
                        low=Decimal(str(quote.low)) if quote.low else None,
                        volume=quote.volume,
                        turnover=Decimal(str(quote.turnover)) if quote.turnover else None
                    )
                )
                await session.execute(stmt)
                await session.commit()

        except Exception as e:
            logger.error(f"Failed to save quote for {quote.symbol}: {e}")

    async def _sync_market_depth_periodically(self, symbols: List[str], interval: int = 5):
        """Periodically sync market depth data."""
        while self.running:
            try:
                for symbol in symbols:
                    if not self.running:
                        break

                    # Get depth data
                    depth = await self.quote_client.get_depth(symbol)
                    if depth:
                        await self._save_market_depth(symbol, depth)

                    # Small delay between requests
                    await asyncio.sleep(0.1)

                # Wait for next interval
                await asyncio.sleep(interval)

            except Exception as e:
                logger.error(f"Failed to sync market depth: {e}")
                await asyncio.sleep(interval)

    async def _save_market_depth(self, symbol: str, depth: openapi.SecurityDepth):
        """Save market depth to database."""
        try:
            async with self.database.session() as session:
                timestamp = datetime.now()

                # Save bid levels
                for i, (price, volume, _, broker_count) in enumerate(depth.bids or []):
                    stmt = insert(MarketDepth).values(
                        symbol=symbol,
                        timestamp=timestamp,
                        position=i + 1,
                        side='BID',
                        price=Decimal(str(price)),
                        volume=volume,
                        broker_count=broker_count
                    ).on_conflict_do_update(
                        index_elements=['symbol', 'timestamp', 'side', 'position'],
                        set_=dict(
                            price=Decimal(str(price)),
                            volume=volume,
                            broker_count=broker_count
                        )
                    )
                    await session.execute(stmt)

                # Save ask levels
                for i, (price, volume, _, broker_count) in enumerate(depth.asks or []):
                    stmt = insert(MarketDepth).values(
                        symbol=symbol,
                        timestamp=timestamp,
                        position=i + 1,
                        side='ASK',
                        price=Decimal(str(price)),
                        volume=volume,
                        broker_count=broker_count
                    ).on_conflict_do_update(
                        index_elements=['symbol', 'timestamp', 'side', 'position'],
                        set_=dict(
                            price=Decimal(str(price)),
                            volume=volume,
                            broker_count=broker_count
                        )
                    )
                    await session.execute(stmt)

                await session.commit()

        except Exception as e:
            logger.error(f"Failed to save market depth for {symbol}: {e}")

    async def _sync_calc_indicators_periodically(self, symbols: List[str], interval: int = 60):
        """Periodically sync calculated indicators."""
        # TODO: get_calc_indexes API not available in current SDK version
        # Disable this feature for now
        logger.warning("Calc indicators sync is disabled (API not available)")
        return

        # while self.running:
        #     try:
        #         # Get calc indicators for all symbols
        #         calc_data = await self.quote_client.get_calc_indexes(
        #             symbols,
        #             [
        #                 openapi.CalcIndex.LastDone,
        #                 openapi.CalcIndex.ChangeRate,
        #                 openapi.CalcIndex.Volume,
        #                 openapi.CalcIndex.Turnover,
        #                 openapi.CalcIndex.YtdChangeRate,
        #                 openapi.CalcIndex.TurnoverRate,
        #                 openapi.CalcIndex.TotalMarketValue,
        #                 openapi.CalcIndex.CapitalFlow,
        #                 openapi.CalcIndex.Amplitude,
        #                 openapi.CalcIndex.VolumeRatio,
        #                 openapi.CalcIndex.PeTtmRatio,
        #                 openapi.CalcIndex.PbRatio,
        #             ]
        #         )
        #
        #         if calc_data:
        #             await self._save_calc_indicators(calc_data)
        #
        #         # Wait for next interval
        #         await asyncio.sleep(interval)
        #
        #     except Exception as e:
        #         logger.error(f"Failed to sync calc indicators: {e}")
        #         await asyncio.sleep(interval)

    async def _save_calc_indicators(self, indicators: List[openapi.SecurityCalcIndex]):
        """Save calculated indicators to database."""
        try:
            async with self.database.session() as session:
                timestamp = datetime.now()

                for ind in indicators:
                    stmt = insert(CalcIndicator).values(
                        symbol=ind.symbol,
                        timestamp=timestamp,
                        pe_ttm=Decimal(str(ind.pe_ttm_ratio)) if hasattr(ind, 'pe_ttm_ratio') and ind.pe_ttm_ratio else None,
                        pb_ratio=Decimal(str(ind.pb_ratio)) if hasattr(ind, 'pb_ratio') and ind.pb_ratio else None,
                        turnover_rate=Decimal(str(ind.turnover_rate)) if hasattr(ind, 'turnover_rate') and ind.turnover_rate else None,
                        volume_ratio=Decimal(str(ind.volume_ratio)) if hasattr(ind, 'volume_ratio') and ind.volume_ratio else None,
                        amplitude=Decimal(str(ind.amplitude)) if hasattr(ind, 'amplitude') and ind.amplitude else None,
                        capital_flow=Decimal(str(ind.capital_flow)) if hasattr(ind, 'capital_flow') and ind.capital_flow else None,
                        ytd_change_rate=Decimal(str(ind.ytd_change_rate)) if hasattr(ind, 'ytd_change_rate') and ind.ytd_change_rate else None,
                        five_day_change=None,  # Not available in CalcIndex
                        ten_day_change=None,
                        half_year_change=None
                    ).on_conflict_do_update(
                        index_elements=['symbol', 'timestamp'],
                        set_=dict(
                            pe_ttm=Decimal(str(ind.pe_ttm_ratio)) if hasattr(ind, 'pe_ttm_ratio') and ind.pe_ttm_ratio else None,
                            pb_ratio=Decimal(str(ind.pb_ratio)) if hasattr(ind, 'pb_ratio') and ind.pb_ratio else None,
                            turnover_rate=Decimal(str(ind.turnover_rate)) if hasattr(ind, 'turnover_rate') and ind.turnover_rate else None,
                            volume_ratio=Decimal(str(ind.volume_ratio)) if hasattr(ind, 'volume_ratio') and ind.volume_ratio else None,
                            amplitude=Decimal(str(ind.amplitude)) if hasattr(ind, 'amplitude') and ind.amplitude else None,
                            capital_flow=Decimal(str(ind.capital_flow)) if hasattr(ind, 'capital_flow') and ind.capital_flow else None,
                            ytd_change_rate=Decimal(str(ind.ytd_change_rate)) if hasattr(ind, 'ytd_change_rate') and ind.ytd_change_rate else None
                        )
                    )
                    await session.execute(stmt)

                await session.commit()
                logger.debug(f"Saved calc indicators for {len(indicators)} symbols")

        except Exception as e:
            logger.error(f"Failed to save calc indicators: {e}")

    async def _sync_minute_klines_periodically(self, symbols: List[str], interval: int = 60):
        """Sync latest minute K-lines periodically."""
        while self.running:
            try:
                now = datetime.now()

                for symbol in symbols:
                    if not self.running:
                        break

                    # Get last minute of K-line data
                    candles = await self.quote_client.get_candlesticks(
                        symbol=symbol,
                        period=openapi.Period.Min_1,
                        count=1,
                        adjust_type=openapi.AdjustType.NoAdjust
                    )

                    if candles:
                        await self._save_minute_kline(symbol, candles[0])

                    # Small delay between requests
                    await asyncio.sleep(0.1)

                # Wait until next minute
                next_minute = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
                wait_seconds = (next_minute - datetime.now()).total_seconds()
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)

            except Exception as e:
                logger.error(f"Failed to sync minute klines: {e}")
                await asyncio.sleep(interval)

    async def _save_minute_kline(self, symbol: str, candle: openapi.Candlestick):
        """Save minute K-line to database."""
        try:
            async with self.database.session() as session:
                stmt = insert(KlineMinute).values(
                    symbol=symbol,
                    timestamp=candle.timestamp,
                    open=Decimal(str(candle.open)),
                    high=Decimal(str(candle.high)),
                    low=Decimal(str(candle.low)),
                    close=Decimal(str(candle.close)),
                    volume=candle.volume,
                    turnover=Decimal(str(candle.turnover)) if candle.turnover else None,
                    trade_count=None,
                    created_at=datetime.now()
                ).on_conflict_do_update(
                    index_elements=['symbol', 'timestamp'],
                    set_=dict(
                        close=Decimal(str(candle.close)),
                        high=Decimal(str(candle.high)),
                        low=Decimal(str(candle.low)),
                        volume=candle.volume,
                        turnover=Decimal(str(candle.turnover)) if candle.turnover else None
                    )
                )
                await session.execute(stmt)
                await session.commit()

        except Exception as e:
            logger.error(f"Failed to save minute kline for {symbol}: {e}")


async def main(
    symbols: List[str] = None,
    config_path: str = "configs/watchlist.yml",
    test_mode: bool = False
):
    """
    Main function to run real-time data sync.

    Args:
        symbols: List of symbols to sync (empty = use watchlist)
        config_path: Path to watchlist config
        test_mode: Run for a short time then exit
    """
    # Initialize settings
    settings = Settings()

    # Get symbols from watchlist if not provided
    if not symbols:
        watchlist_loader = WatchlistLoader(Path(config_path))
        watchlist = watchlist_loader.load()
        symbols = [f"{item.symbol}.{item.market}" for item in watchlist]
        logger.info(f"Loaded {len(symbols)} symbols from watchlist")

    # Create sync service
    sync_service = RealtimeDataSync(settings)

    # Handle signals
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, stopping...")
        asyncio.create_task(sync_service.stop())

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Start sync
        if test_mode:
            # Run for 30 seconds in test mode
            logger.info("Running in test mode for 30 seconds...")
            task = asyncio.create_task(sync_service.start(symbols))
            await asyncio.sleep(30)
            await sync_service.stop()
            await task
        else:
            # Run continuously
            await sync_service.start(symbols)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        raise
    finally:
        await sync_service.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real-time data synchronization")

    parser.add_argument(
        "symbols",
        nargs="*",
        help="Symbols to sync (e.g., 700.HK AAPL.US). Leave empty to use watchlist"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="configs/watchlist.yml",
        help="Path to watchlist config (default: configs/watchlist.yml)"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run in test mode (30 seconds)"
    )

    args = parser.parse_args()

    # Configure logging
    logger.add(
        "logs/realtime_sync_{time}.log",
        rotation="1 day",
        retention="7 days",
        level="INFO"
    )

    # Run sync
    asyncio.run(main(
        symbols=args.symbols,
        config_path=args.config,
        test_mode=args.test
    ))