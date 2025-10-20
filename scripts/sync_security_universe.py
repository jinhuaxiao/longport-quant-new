#!/usr/bin/env python3
"""Sync security universe and static info for a given market."""

from __future__ import annotations

import argparse
import asyncio
from typing import Dict

from loguru import logger
from longport import openapi

from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.data.security_sync import SecurityUniverseSync
from longport_quant.persistence.db import DatabaseSessionManager


MARKET_ALIASES: Dict[str, openapi.Market] = {
    "hk": openapi.Market.HK,
    "us": openapi.Market.US,
    "sg": openapi.Market.SG,
    "cn": openapi.Market.CN,
}


def resolve_market(alias: str) -> openapi.Market:
    key = alias.lower()
    if key not in MARKET_ALIASES:
        raise ValueError(f"Unsupported market alias: {alias}")
    return MARKET_ALIASES[key]


async def main(
    market_alias: str,
    include_static: bool,
    batch_size: int,
    category: str | None,
) -> None:
    settings = get_settings()
    market = resolve_market(market_alias)

    async with DatabaseSessionManager(settings.database_dsn) as db:
        async with QuoteDataClient(settings) as quote_client:
            syncer = SecurityUniverseSync(settings, db, quote_client)
            stats = await syncer.sync_market(
                market,
                include_static=include_static,
                batch_size=batch_size,
                category=category,
            )
            logger.info(
                "Universe sync finished for {}: {} symbols, {} static rows",
                market_alias.upper(),
                stats["universe"],
                stats["static"],
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync security universe and fundamentals")
    parser.add_argument(
        "market",
        help="Market alias (hk/us/sg/cn)",
    )
    parser.add_argument(
        "--no-static",
        action="store_true",
        help="Skip syncing static fundamentals",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=400,
        help="Batch size when fetching static info (<=500 recommended)",
    )
    parser.add_argument(
        "--category",
        type=str,
        help="Optional security list category (e.g. overnight)",
    )

    args = parser.parse_args()
    logger.add("logs/sync_security_universe_{time}.log", rotation="1 day", retention="7 days")

    asyncio.run(
        main(
            market_alias=args.market,
            include_static=not args.no_static,
            batch_size=args.batch_size,
            category=args.category,
        )
    )
