"""Shared signal dispatch coordination."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional

from loguru import logger

from longport_quant.notifications import SlackNotifier
from longport_quant.common.types import Signal


@dataclass
class SignalRecord:
    side: str
    timestamp: datetime
    quantity: float


class SignalDispatcher:
    """Coordinate signal submission, detect conflicts, and emit notifications."""

    def __init__(
        self,
        order_router,
        slack: SlackNotifier | None = None,
        conflict_cooldown_seconds: int = 60,
    ) -> None:
        self._order_router = order_router
        self._slack = slack
        self._cooldown = timedelta(seconds=conflict_cooldown_seconds)
        self._records: Dict[str, SignalRecord] = {}
        self._lock = asyncio.Lock()

    async def dispatch(self, signal: Signal) -> dict | None:
        async with self._lock:
            conflict = self._check_conflict(signal)
            if conflict:
                message = (
                    f"Signal conflict for {signal.symbol}: new {signal.side} "
                    f"vs. last {conflict.side} at {conflict.timestamp.isoformat()}"
                )
                logger.warning(message)
                await self._notify(message)
                return None

            response = await self._order_router.submit(
                {
                    "symbol": signal.symbol,
                    "side": signal.side,
                    "quantity": signal.quantity,
                    "price": signal.price,
                }
            )

            self._records[signal.symbol] = SignalRecord(
                side=signal.side.upper(),
                timestamp=datetime.utcnow(),
                quantity=signal.quantity,
            )

            await self._notify(
                f"Submitted {signal.side} {signal.symbol} x {signal.quantity} @ {signal.price}"
            )
            return response

    def acknowledge_fill(self, symbol: str, side: str) -> None:
        record = self._records.get(symbol)
        if record and record.side != side.upper():
            # Reset when execution confirmed on opposite side.
            self._records.pop(symbol, None)

    def _check_conflict(self, signal: Signal) -> Optional[SignalRecord]:
        record = self._records.get(signal.symbol)
        if not record:
            return None
        if record.side == signal.side.upper():
            return None
        if datetime.utcnow() - record.timestamp > self._cooldown:
            return None
        return record

    async def _notify(self, message: str) -> None:
        if self._slack:
            await self._slack.send(message)


__all__ = ["SignalDispatcher", "SignalRecord"]

