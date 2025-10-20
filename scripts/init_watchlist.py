#!/usr/bin/env python3
"""Initialize watchlist in database and sync basic data."""

import asyncio
import argparse
from pathlib import Path
import sys
import yaml
from typing import List, Dict, Any

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from loguru import logger
from longport_quant.config.settings import Settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import SecurityUniverse, SecurityStatic
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.data.kline_sync import KlineDataService
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import text


class WatchlistInitializer:
    """Initialize and manage watchlist in database."""

    def __init__(self, settings: Settings, db: DatabaseSessionManager):
        self.settings = settings
        self.db = db
        self.quote_client = QuoteDataClient(settings)

    async def load_watchlist_config(self, config_path: str) -> Dict[str, Any]:
        """Load watchlist configuration from YAML file."""
        config_file = Path(config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Watchlist config not found: {config_path}")

        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        logger.info(f"Loaded watchlist config from {config_path}")
        return config

    async def parse_symbols(self, config: Dict[str, Any]) -> List[Dict[str, str]]:
        """Parse symbols from config into structured format."""
        symbols = []

        # Parse market-specific symbols
        if 'markets' in config:
            for market, symbol_list in config['markets'].items():
                for symbol_str in symbol_list:
                    # Handle comments
                    if '#' in symbol_str:
                        symbol_str = symbol_str.split('#')[0].strip()

                    if symbol_str:
                        # Extract symbol and description
                        parts = symbol_str.split()
                        symbol = parts[0]

                        # Handle HK market suffix
                        if market == 'hk' and not symbol.endswith('.HK'):
                            symbol = symbol  # Already has .HK suffix in config
                        elif market == 'sz' and not symbol.endswith('.SZ'):
                            symbol = symbol  # Already has .SZ suffix
                        elif market == 'us':
                            symbol = f"{symbol}.US"

                        # Remove market suffix for database storage
                        clean_symbol = symbol.split('.')[0]

                        symbols.append({
                            'symbol': clean_symbol,
                            'market': market.upper(),
                            'full_symbol': symbol,
                            'description': None
                        })

        # Parse explicit symbol list
        if 'symbols' in config and config['symbols']:
            for symbol_str in config['symbols']:
                if symbol_str:
                    symbols.append({
                        'symbol': symbol_str,
                        'market': 'CUSTOM',
                        'full_symbol': symbol_str,
                        'description': None
                    })

        logger.info(f"Parsed {len(symbols)} symbols from config")
        return symbols

    async def sync_watchlist_to_db(self, symbols: List[Dict[str, str]]) -> int:
        """Sync watchlist symbols to database using security_universe table."""
        async with self.db.session() as session:
            count = 0
            for symbol_info in symbols:
                # Use security_universe table instead of watchsymbol
                stmt = insert(SecurityUniverse).values(
                    symbol=symbol_info['full_symbol'],  # Store full symbol with market suffix
                    market=symbol_info['market'],
                    name_cn=symbol_info['description'],
                    name_en=symbol_info['description']
                ).on_conflict_do_update(
                    index_elements=['symbol'],
                    set_=dict(
                        market=symbol_info['market'],
                        name_cn=symbol_info['description'],
                        name_en=symbol_info['description']
                    )
                )
                await session.execute(stmt)
                count += 1

            await session.commit()

        logger.info(f"Synced {count} symbols to security_universe table")
        return count

    async def sync_static_info(self, symbols: List[Dict[str, str]]) -> int:
        """Sync static information for watchlist symbols."""
        # Get full symbols for API call
        full_symbols = [s['full_symbol'] for s in symbols]

        # Batch fetch static info (max 500 per request)
        batch_size = 500
        all_static_info = []

        for i in range(0, len(full_symbols), batch_size):
            batch = full_symbols[i:i + batch_size]
            logger.info(f"Fetching static info for batch {i//batch_size + 1}/{(len(full_symbols)-1)//batch_size + 1}")

            try:
                static_infos = await self.quote_client.get_static_info(batch)
                all_static_info.extend(static_infos)
            except Exception as e:
                logger.error(f"Failed to fetch static info for batch: {e}")
                continue

        # Store in database
        if all_static_info:
            kline_service = KlineDataService(self.settings, self.db, self.quote_client)
            count = await kline_service.sync_security_static(
                [info.symbol for info in all_static_info]
            )
            return count

        return 0

    async def verify_watchlist(self) -> Dict[str, int]:
        """Verify watchlist data in database."""
        async with self.db.session() as session:
            # Count security universe symbols (our watchlist)
            universe_count = await session.execute(
                text("SELECT COUNT(*) FROM security_universe")
            )
            universe_total = universe_count.scalar()

            # Count by market
            market_counts = await session.execute(
                text("SELECT market, COUNT(*) FROM security_universe GROUP BY market")
            )
            market_stats = {row[0]: row[1] for row in market_counts}

            # Count static info
            static_count = await session.execute(
                text("SELECT COUNT(*) FROM security_static")
            )
            static_total = static_count.scalar()

        return {
            'watchlist_total': universe_total,
            'static_info_total': static_total,
            'markets': market_stats
        }


async def main(
    config_path: str,
    sync_static: bool = True,
    sync_daily: bool = False,
    sync_minute: bool = False
):
    """
    Main function to initialize watchlist.

    Args:
        config_path: Path to watchlist YAML config
        sync_static: Whether to sync static information
        sync_daily: Whether to sync daily K-lines
        sync_minute: Whether to sync minute K-lines
    """
    # Initialize components
    settings = Settings()
    db = DatabaseSessionManager(settings.database_dsn)

    # Initialize database session
    async with db as database:
        # Initialize watchlist manager
        watchlist_init = WatchlistInitializer(settings, database)

        try:
            # Load and parse watchlist config
            config = await watchlist_init.load_watchlist_config(config_path)
            symbols = await watchlist_init.parse_symbols(config)

            # Sync to database
            logger.info("Syncing watchlist to database...")
            count = await watchlist_init.sync_watchlist_to_db(symbols)

            # Sync static information
            if sync_static:
                logger.info("Syncing security static information...")
                static_count = await watchlist_init.sync_static_info(symbols)
                logger.info(f"Updated {static_count} security static records")

            # Optionally sync K-line data
            if sync_daily or sync_minute:
                from longport_quant.data.kline_sync import KlineDataService

                kline_service = KlineDataService(settings, database, watchlist_init.quote_client)
                full_symbols = [s['full_symbol'] for s in symbols]

                if sync_daily:
                    logger.info("Syncing daily K-lines...")
                    results = await kline_service.sync_daily_klines(full_symbols)
                    total = sum(v for v in results.values() if v > 0)
                    logger.info(f"Synced {total} daily K-line records")

                if sync_minute:
                    logger.info("Syncing minute K-lines (last 7 days for testing)...")
                    results = await kline_service.sync_minute_klines(full_symbols, days_back=7)
                    total = sum(v for v in results.values() if v > 0)
                    logger.info(f"Synced {total} minute K-line records")

            # Verify results
            stats = await watchlist_init.verify_watchlist()
            logger.info("Watchlist initialization completed:")
            logger.info(f"  Total symbols: {stats['watchlist_total']}")
            logger.info(f"  Static info: {stats['static_info_total']}")
            logger.info(f"  Markets: {stats['markets']}")

        except Exception as e:
            logger.error(f"Initialization failed: {e}")
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize watchlist in database")

    parser.add_argument(
        "--config",
        type=str,
        default="configs/watchlist.yml",
        help="Path to watchlist YAML config (default: configs/watchlist.yml)"
    )
    parser.add_argument(
        "--no-static",
        action="store_true",
        help="Skip syncing static information"
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="Also sync daily K-lines"
    )
    parser.add_argument(
        "--minute",
        action="store_true",
        help="Also sync minute K-lines (last 7 days for testing)"
    )

    args = parser.parse_args()

    # Configure logging
    logger.add(
        "logs/init_watchlist_{time}.log",
        rotation="1 day",
        retention="7 days",
        level="INFO"
    )

    # Run initialization
    asyncio.run(main(
        config_path=args.config,
        sync_static=not args.no_static,
        sync_daily=args.daily,
        sync_minute=args.minute
    ))