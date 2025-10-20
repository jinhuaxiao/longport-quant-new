#!/usr/bin/env python3
"""Test script for enhanced market data service."""

import asyncio
import sys
import os
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from longport_quant.config.settings import get_settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.data.enhanced_market_data import (
    EnhancedMarketDataService,
    MarketDataConfig,
    DataType,
)


class MarketDataMonitor:
    """Monitor and display market data."""

    def __init__(self):
        self.quote_count = 0
        self.depth_count = 0
        self.trade_count = 0
        self.last_quotes = {}

    async def handle_quote(self, data: dict):
        """Handle quote data."""
        self.quote_count += 1
        symbol = data["symbol"]
        price = data.get("last_price")
        bid = data.get("bid_price")
        ask = data.get("ask_price")
        volume = data.get("volume")

        self.last_quotes[symbol] = data

        logger.info(
            f"[QUOTE] {symbol}: ${price:.2f} "
            f"(Bid: ${bid:.2f}, Ask: ${ask:.2f}, Vol: {volume:,})"
        )

    async def handle_depth(self, data: dict):
        """Handle market depth data."""
        self.depth_count += 1
        symbol = data["symbol"]
        bid_prices = data.get("bid_prices", [])
        ask_prices = data.get("ask_prices", [])

        if bid_prices and ask_prices:
            spread = ask_prices[0] - bid_prices[0] if ask_prices[0] and bid_prices[0] else 0
            logger.info(
                f"[DEPTH] {symbol}: "
                f"Best Bid: ${bid_prices[0]:.2f}, "
                f"Best Ask: ${ask_prices[0]:.2f}, "
                f"Spread: ${spread:.2f}"
            )

    async def handle_trade(self, data: dict):
        """Handle trade tick data."""
        self.trade_count += 1
        symbol = data["symbol"]
        price = data.get("price")
        volume = data.get("volume")
        direction = data.get("direction", "unknown")

        logger.info(
            f"[TRADE] {symbol}: ${price:.2f} x {volume} ({direction})"
        )

    def print_summary(self):
        """Print summary statistics."""
        print("\n" + "=" * 60)
        print("MARKET DATA SUMMARY")
        print("=" * 60)
        print(f"Total Quotes: {self.quote_count}")
        print(f"Total Depth Updates: {self.depth_count}")
        print(f"Total Trades: {self.trade_count}")

        if self.last_quotes:
            print("\nLatest Quotes:")
            for symbol, quote in list(self.last_quotes.items())[:5]:
                price = quote.get("last_price", 0)
                print(f"  {symbol}: ${price:.2f}")

        print("=" * 60)


async def main():
    """Main entry point."""
    # Setup logging
    logger.add(
        f"market_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
        rotation="100 MB",
        retention="7 days",
        level="DEBUG"
    )

    # Load settings
    settings = get_settings()

    # Initialize database
    db = DatabaseSessionManager(settings.database_dsn)

    # Configure market data service
    config = MarketDataConfig(
        enable_quote=True,
        enable_depth=True,
        enable_trade=True,
        enable_broker=False,
        persist_to_db=True,
        queue_size=10000,
        batch_size=100,
        flush_interval=1.0,
        reconnect_delay=5.0,
        max_reconnect_attempts=10,
    )

    # Create monitor
    monitor = MarketDataMonitor()

    try:
        # Start market data service
        async with EnhancedMarketDataService(settings, db, config) as service:
            # Subscribe to data types
            service.subscribe(DataType.QUOTE, monitor.handle_quote)
            service.subscribe(DataType.DEPTH, monitor.handle_depth)
            service.subscribe(DataType.TRADE, monitor.handle_trade)

            logger.info("Market data service started successfully")
            logger.info("Press Ctrl+C to stop")

            # Run for a while
            for i in range(60):  # Run for 60 seconds
                await asyncio.sleep(1)

                # Print status every 10 seconds
                if (i + 1) % 10 == 0:
                    status = service.get_status()
                    logger.info(
                        f"Status - Connected: {status.connected}, "
                        f"Messages: {status.total_messages}, "
                        f"Types: {status.messages_by_type}"
                    )

            # Print final summary
            monitor.print_summary()

    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    except Exception as e:
        logger.error(f"Error in main: {e}", exc_info=True)
    finally:
        await db.close()
        logger.info("Market data service stopped")


if __name__ == "__main__":
    asyncio.run(main())