#!/usr/bin/env python
"""Fetch trading calendars from Longport OpenAPI and persist them locally."""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

from loguru import logger
from longport import OpenApiException, openapi


def _ensure_src_on_path() -> None:
    project_root = Path(__file__).resolve().parents[1]
    src_path = project_root / "src"
    if src_path.exists():
        sys.path.insert(0, str(src_path))


_ensure_src_on_path()

from longport_quant.config import get_settings  # noqa: E402
from longport_quant.config.sdk import build_sdk_config  # noqa: E402
from longport_quant.data.watchlist import WatchlistLoader  # noqa: E402
from longport_quant.services.calendar import CalendarDay, upsert_calendar  # noqa: E402
from scripts.run_strategy import (  # noqa: E402 - reuse market config helpers
    CALENDAR_LOOKAHEAD_DAYS,
    MARKET_CONFIG,
    _build_calendar_entries,
)


SYNC_HORIZON_DAYS = max(60, CALENDAR_LOOKAHEAD_DAYS)


def _resolve_markets() -> list[str]:
    watchlist = WatchlistLoader().load()
    markets = sorted({item.market.lower() for item in watchlist})
    return [market for market in markets if market in MARKET_CONFIG]


async def _persist_calendar(dsn: str, entries: list[CalendarDay]) -> None:
    await upsert_calendar(dsn, entries)


def main() -> None:
    settings = get_settings()
    candidate_markets = _resolve_markets()
    if not candidate_markets:
        logger.warning("监控列表中没有可同步的市场")
        return

    try:
        config = build_sdk_config(settings)
        quote = openapi.QuoteContext(config)
    except OpenApiException as exc:
        logger.error("初始化 QuoteContext 失败，无法同步: {}", exc)
        return

    today = date.today()
    entries = _build_calendar_entries(quote, candidate_markets, today, SYNC_HORIZON_DAYS)

    del quote  # Ensure SDK context is released before async operations

    if not entries:
        logger.warning("未生成任何交易日日历条目")
        return

    asyncio.run(_persist_calendar(settings.database_dsn, entries))
    logger.info("交易日历同步完成，共更新 {} 条记录", len(entries))


if __name__ == "__main__":
    main()
