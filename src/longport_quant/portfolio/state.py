"""Portfolio state service backed by persistence layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from loguru import logger

from longport_quant.persistence.db import DatabaseSessionManager


PositionMap = Dict[str, float]


@dataclass
class PortfolioSnapshot:
    cash: float
    positions: PositionMap


class PortfolioService:
    def __init__(self, db_manager: DatabaseSessionManager) -> None:
        self._db_manager = db_manager
        self._cache: PortfolioSnapshot | None = None

    async def refresh(self) -> None:
        # Placeholder: replace with real DB fetch or API call
        self._cache = PortfolioSnapshot(cash=1_000_000.0, positions={})
        logger.debug("Portfolio snapshot refreshed: {}", self._cache)

    def position_size(self, symbol: str) -> float:
        if not self._cache:
            return 0.0
        return self._cache.positions.get(symbol, 0.0)

    async def update_position(self, symbol: str, delta: float) -> None:
        if not self._cache:
            await self.refresh()
        if self._cache:
            current = self._cache.positions.get(symbol, 0.0)
            self._cache.positions[symbol] = current + delta
            logger.debug(
                "Position for {} updated to {}", symbol, self._cache.positions[symbol]
            )
