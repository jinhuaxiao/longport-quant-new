#!/usr/bin/env python
"""Sync the configured watchlist into the database."""

import asyncio

from loguru import logger
from sqlalchemy.dialects.postgresql import insert

from longport_quant.config import get_settings
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence import models


async def main() -> None:
    settings = get_settings()
    loader = WatchlistLoader()
    watchlist = loader.load()

    async with DatabaseSessionManager(settings.database_dsn) as db:
        async with db.session() as session:
            table = models.WatchSymbol.__table__
            for item in watchlist:
                values = {"symbol": item.symbol, "market": item.market}
                stmt = insert(table).values(**values).on_conflict_do_nothing(
                    index_elements=[table.c.symbol, table.c.market]
                )
                await session.execute(stmt)
            await session.commit()
    logger.info("Watchlist synced with {} symbols", len(watchlist.items))


if __name__ == "__main__":
    asyncio.run(main())
