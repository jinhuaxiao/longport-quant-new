#!/usr/bin/env python
"""CLI entrypoint to start the trading stack."""

from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def _ensure_src_on_path() -> None:
    """Allow running the script without installing the package."""

    project_root = Path(__file__).resolve().parents[1]
    src_path = project_root / "src"
    if src_path.exists():
        sys.path.insert(0, str(src_path))
_ensure_src_on_path()

from loguru import logger  # noqa: E402
from longport import OpenApiException, openapi  # noqa: E402
from longport_quant.config import get_settings  # noqa: E402  (import after path setup)
from longport_quant.config.sdk import build_sdk_config  # noqa: E402
from longport_quant.core.app import run  # noqa: E402  (import after path setup)
from longport_quant.data.watchlist import Watchlist, WatchlistLoader  # noqa: E402
from longport_quant.services.calendar import CalendarDay, load_calendar, upsert_calendar  # noqa: E402


UTC = ZoneInfo("UTC")
CALENDAR_LOOKAHEAD_DAYS = 30


MARKET_CONFIG = {
    "hk": {
        "api_market": openapi.Market.HK,
        "session_key": "hk",
        "timezone": "Asia/Hong_Kong",
    },
    "us": {
        "api_market": openapi.Market.US,
        "session_key": "us",
        "timezone": "America/New_York",
    },
    "sz": {
        "api_market": openapi.Market.CN,
        "session_key": "cn",
        "timezone": "Asia/Shanghai",
    },
    "cn": {
        "api_market": openapi.Market.CN,
        "session_key": "cn",
        "timezone": "Asia/Shanghai",
    },
    "sh": {
        "api_market": openapi.Market.CN,
        "session_key": "cn",
        "timezone": "Asia/Shanghai",
    },
    "sg": {
        "api_market": openapi.Market.SG,
        "session_key": "sg",
        "timezone": "Asia/Singapore",
    },
}


def _parse_hms(raw: time | str) -> time:
    if isinstance(raw, time):
        return raw

    return time.fromisoformat(str(raw))


def _collect_intraday_sessions(quote: openapi.QuoteContext) -> dict[str, list[tuple[time, time]]]:
    try:
        raw_sessions = quote.trading_session()
    except OpenApiException as exc:
        logger.warning("无法获取交易时段信息: {}", exc)
        return {}

    sessions: dict[str, list[tuple[time, time]]] = {}
    for entry in raw_sessions:
        key = str(entry.market).split(".")[-1].lower()
        intraday: list[tuple[time, time]] = []
        for window in entry.trade_sessions:
            if window.trade_session != openapi.TradeSession.Intraday:
                continue
            intraday.append((_parse_hms(window.begin_time), _parse_hms(window.end_time)))
        sessions[key] = intraday
    return sessions


def _fetch_trading_day_sets(
    quote: openapi.QuoteContext,
    market: openapi.Market,
    start: date,
    horizon_days: int = 30,
) -> tuple[set[date], set[date]]:
    try:
        window = quote.trading_days(market, start, start + timedelta(days=horizon_days))
    except OpenApiException as exc:
        logger.warning(
            "获取市场{}交易日失败: {}",
            str(market).split(".")[-1].lower(),
            exc,
        )
        return set(), set()

    trading = set(getattr(window, "trading_days", []) or [])
    half = set(getattr(window, "half_trading_days", []) or [])
    return trading, half


def _build_calendar_entries(
    quote: openapi.QuoteContext,
    markets: list[str],
    start: date,
    horizon_days: int,
) -> list[CalendarDay]:
    sessions = _collect_intraday_sessions(quote)
    entries: list[CalendarDay] = []

    for market in markets:
        meta = MARKET_CONFIG[market]
        intraday = sessions.get(meta["session_key"])
        if not intraday:
            logger.warning("市场{}缺少日内时间段信息，跳过", market)
            continue

        trading_days, half_days = _fetch_trading_day_sets(
            quote,
            meta["api_market"],
            start - timedelta(days=7),
            horizon_days + 7,
        )
        if not trading_days and not half_days:
            continue

        all_days = sorted({*trading_days, *half_days})
        for trading_day in all_days:
            is_half_day = trading_day in half_days
            sessions_for_day = intraday[:1] if is_half_day else intraday
            entries.append(
                CalendarDay(
                    market=market,
                    trade_date=trading_day,
                    sessions=list(sessions_for_day),
                    is_half_day=is_half_day,
                )
            )

    return entries


def _load_calendar_window(
    settings,
    markets: list[str],
    today: date,
    horizon_days: int,
) -> dict[str, list[CalendarDay]]:
    if not markets:
        return {}
    window_start = today - timedelta(days=1)
    window_end = today + timedelta(days=horizon_days)
    return asyncio.run(
        load_calendar(
            settings.database_dsn,
            markets,
            window_start,
            window_end,
        )
    )


def _store_calendar_entries(settings, entries: list[CalendarDay]) -> None:
    if not entries:
        return
    asyncio.run(upsert_calendar(settings.database_dsn, entries))


def _has_future_entries(days: list[CalendarDay], today: date) -> bool:
    return any(entry.trade_date >= today for entry in days)


def _fetch_calendar_and_store(
    settings,
    markets: list[str],
    today: date,
) -> dict[str, list[CalendarDay]]:
    if not markets:
        return {}

    try:
        config = build_sdk_config(settings)
        quote = openapi.QuoteContext(config)
    except OpenApiException as exc:
        logger.warning("补充交易日历时初始化 QuoteContext 失败: {}", exc)
        return {}

    try:
        entries = _build_calendar_entries(quote, markets, today, CALENDAR_LOOKAHEAD_DAYS)
    finally:
        del quote

    if not entries:
        return {}

    _store_calendar_entries(settings, entries)
    return _load_calendar_window(settings, markets, today, CALENDAR_LOOKAHEAD_DAYS)


def _ensure_calendar(
    settings,
    markets: list[str],
    today: date,
) -> dict[str, list[CalendarDay]]:
    calendar = _load_calendar_window(settings, markets, today, CALENDAR_LOOKAHEAD_DAYS)
    missing = [
        market
        for market in markets
        if not _has_future_entries(calendar.get(market, []), today)
    ]
    if missing:
        logger.info("交易日历缓存缺失市场{}，尝试从 OpenAPI 补充", missing)
        refreshed = _fetch_calendar_and_store(settings, missing, today)
        if refreshed:
            calendar.update(refreshed)
        else:
            logger.warning("未能补充交易日历，使用现有缓存")
    return calendar


def _select_markets_from_calendar(
    markets: list[str],
    calendar: dict[str, list[CalendarDay]],
) -> list[str]:
    active: list[str] = []
    upcoming: list[tuple[datetime, str]] = []

    for market in markets:
        meta = MARKET_CONFIG[market]
        tz = ZoneInfo(meta["timezone"])
        now_market = datetime.now(tz)
        entries = [day for day in calendar.get(market, []) if day.trade_date >= now_market.date()]
        if not entries:
            continue

        for entry in entries:
            if not entry.sessions:
                continue
            for begin, end in entry.sessions:
                start_dt = datetime.combine(entry.trade_date, begin, tzinfo=tz)
                end_dt = datetime.combine(entry.trade_date, end, tzinfo=tz)
                if start_dt <= now_market <= end_dt:
                    active.append(market)
                    break
                if now_market < start_dt:
                    upcoming.append((start_dt, market))
                    break
            if market in active:
                break

    if active:
        deduped: list[str] = []
        for market in active:
            if market not in deduped:
                deduped.append(market)
        logger.info("当前活跃市场: {}", deduped)
        return deduped

    if upcoming:
        earliest = min(upcoming, key=lambda item: item[0].astimezone(UTC))
        target_utc = earliest[0].astimezone(UTC)
        markets_ready = sorted(
            {
                market
                for moment, market in upcoming
                if moment.astimezone(UTC) == target_utc
            }
        )
        logger.info(
            "未处于交易时段，选择最近开盘市场 {} -> {}",
            earliest[0],
            markets_ready,
        )
        return markets_ready

    return []


def _determine_markets_via_api(settings, markets: list[str]) -> list[str]:
    if not markets:
        return []

    try:
        config = build_sdk_config(settings)
        quote = openapi.QuoteContext(config)
    except OpenApiException as exc:
        logger.warning("初始化 QuoteContext 失败，回退全部市场: {}", exc)
        return markets

    try:
        sessions = _collect_intraday_sessions(quote)
        if not sessions:
            return markets

        active: list[str] = []
        upcoming: list[tuple[datetime, str]] = []

        for market in markets:
            meta = MARKET_CONFIG[market]
            windows = sessions.get(meta["session_key"])
            if not windows:
                logger.debug("市场{}缺少日内时段信息，跳过", market)
                continue

            tz = ZoneInfo(meta["timezone"])
            now_market = datetime.now(tz)
            trading_days, half_days = _fetch_trading_day_sets(
                quote,
                meta["api_market"],
                now_market.date(),
                CALENDAR_LOOKAHEAD_DAYS,
            )
            combined = sorted({*trading_days, *half_days})
            if not combined:
                continue

            if now_market.date() in combined:
                next_start: datetime | None = None
                for begin, end in windows:
                    start_dt = datetime.combine(now_market.date(), begin, tzinfo=tz)
                    end_dt = datetime.combine(now_market.date(), end, tzinfo=tz)
                    if start_dt <= now_market <= end_dt:
                        active.append(market)
                        break
                    if now_market < start_dt:
                        next_start = start_dt
                        break
                if market not in active and next_start:
                    upcoming.append((next_start, market))
            else:
                future = [day for day in combined if day > now_market.date()]
                if not future:
                    continue
                next_day = future[0]
                start_dt = datetime.combine(next_day, windows[0][0], tzinfo=tz)
                upcoming.append((start_dt, market))

    finally:
        del quote

    if active:
        deduped: list[str] = []
        for market in active:
            if market not in deduped:
                deduped.append(market)
        logger.info("当前活跃市场: {}", deduped)
        return deduped

    if upcoming:
        earliest = min(upcoming, key=lambda item: item[0].astimezone(UTC))
        target_utc = earliest[0].astimezone(UTC)
        markets_ready = sorted(
            {
                market
                for moment, market in upcoming
                if moment.astimezone(UTC) == target_utc
            }
        )
        logger.info(
            "未处于交易时段，选择最近开盘市场 {} -> {}",
            earliest[0],
            markets_ready,
        )
        return markets_ready

    logger.warning("无法从接口确定交易市场，回退全部候选: {}", markets)
    return markets


def _determine_active_markets(settings, watchlist: Watchlist) -> list[str]:
    candidates = sorted({item.market.lower() for item in watchlist})
    candidates = [market for market in candidates if market in MARKET_CONFIG]
    if not candidates:
        logger.warning("当前监控列表中没有匹配的市场，默认返回空列表")
        return []

    today = date.today()
    calendar = _ensure_calendar(settings, candidates, today)
    selection = _select_markets_from_calendar(candidates, calendar)
    if selection:
        return selection

    logger.warning("交易日历无法提供活跃市场信息，回退实时接口计算")
    return _determine_markets_via_api(settings, candidates)


if __name__ == "__main__":
    settings = get_settings()
    watchlist = WatchlistLoader().load()
    settings.active_markets = _determine_active_markets(settings, watchlist)
    run()
