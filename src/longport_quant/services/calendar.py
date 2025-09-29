"""Trading calendar persistence helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Dict, Iterable, List, Sequence

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import TradingCalendar


@dataclass(frozen=True)
class CalendarDay:
    market: str
    trade_date: date
    sessions: list[tuple[time, time]]
    is_half_day: bool


def _decode_sessions(payload: Sequence[dict]) -> list[tuple[time, time]]:
    sessions: list[tuple[time, time]] = []
    for item in payload:
        begin = item.get("begin")
        end = item.get("end")
        if begin is None or end is None:
            continue
        if isinstance(begin, str):
            begin_time = time.fromisoformat(begin)
        else:
            begin_time = begin
        if isinstance(end, str):
            end_time = time.fromisoformat(end)
        else:
            end_time = end
        sessions.append((begin_time, end_time))
    return sessions


def _encode_sessions(payload: Iterable[tuple[time, time]]) -> list[dict[str, str]]:
    return [
        {"begin": begin.isoformat(), "end": end.isoformat()}
        for begin, end in payload
    ]


async def load_calendar(
    dsn: str,
    markets: Sequence[str],
    start: date,
    end: date,
) -> Dict[str, list[CalendarDay]]:
    """Fetch calendar rows from the database within the given window."""

    result: Dict[str, list[CalendarDay]] = {market: [] for market in markets}
    async with DatabaseSessionManager(dsn) as db:
        async with db.session() as session:
            stmt = (
                select(TradingCalendar)
                .where(TradingCalendar.market.in_(markets))
                .where(TradingCalendar.trade_date >= start)
                .where(TradingCalendar.trade_date <= end)
                .order_by(TradingCalendar.trade_date.asc())
            )
            rows = await session.execute(stmt)
            for row in rows.scalars():
                result[row.market].append(
                    CalendarDay(
                        market=row.market,
                        trade_date=row.trade_date,
                        sessions=_decode_sessions(row.sessions or []),
                        is_half_day=row.is_half_day,
                    )
                )
    return result


async def upsert_calendar(dsn: str, entries: Sequence[CalendarDay], source: str = "longport_api") -> None:
    if not entries:
        return

    payload = [
        {
            "market": entry.market,
            "trade_date": entry.trade_date,
            "sessions": _encode_sessions(entry.sessions),
            "is_half_day": entry.is_half_day,
            "source": source,
        }
        for entry in entries
    ]

    async with DatabaseSessionManager(dsn) as db:
        async with db.session() as session:
            insert_stmt = insert(TradingCalendar).values(payload)
            stmt = insert_stmt.on_conflict_do_update(
                index_elements=[TradingCalendar.market, TradingCalendar.trade_date],
                set_={
                    "sessions": insert_stmt.excluded.sessions,
                    "is_half_day": insert_stmt.excluded.is_half_day,
                    "source": insert_stmt.excluded.source,
                },
            )
            await session.execute(stmt)
            await session.commit()


async def purge_calendar_before(dsn: str, markets: Sequence[str], cutoff: date) -> None:
    async with DatabaseSessionManager(dsn) as db:
        async with db.session() as session:
            stmt = (
                delete(TradingCalendar)
                .where(TradingCalendar.market.in_(markets))
                .where(TradingCalendar.trade_date < cutoff)
            )
            await session.execute(stmt)
            await session.commit()
