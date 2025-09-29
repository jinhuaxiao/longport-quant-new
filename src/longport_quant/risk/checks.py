"""Risk validation for strategies and orders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from loguru import logger

from longport_quant.config.settings import Settings
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.portfolio.state import PortfolioService


@dataclass
class RiskLimits:
    max_notional: float
    max_position: float


class RiskEngine:
    def __init__(self, settings: Settings, portfolio: PortfolioService) -> None:
        self._settings = settings
        self._portfolio = portfolio
        self._limits: Dict[str, RiskLimits] = {}
        self._watchlist = WatchlistLoader().load()

    def validate_order(self, order: dict) -> bool:
        symbol = order.get("symbol")
        if symbol not in self._watchlist.symbols():
            logger.error("Order for {} rejected: not in watchlist", symbol)
            return False

        limits = self._limits.get(symbol)
        if limits is None:
            logger.debug("No explicit limits for {}, using default allow", symbol)
            return True

        quantity = float(order.get("quantity", 0))
        price = float(order.get("price", 0))
        notional = quantity * price
        position = self._portfolio.position_size(symbol)

        if notional > limits.max_notional:
            logger.warning(
                "Order notional {notional:.2f} exceeds limit {limit:.2f}",
                notional=notional,
                limit=limits.max_notional,
            )
            return False
        if abs(position + quantity) > limits.max_position:
            logger.warning(
                "Position {position:.2f} would exceed limit {limit:.2f}",
                position=position + quantity,
                limit=limits.max_position,
            )
            return False
        return True

    def set_limit(self, symbol: str, max_notional: float, max_position: float) -> None:
        self._limits[symbol] = RiskLimits(max_notional=max_notional, max_position=max_position)
        logger.info(
            "Set risk limits for {symbol}: notional={max_notional:.2f}, position={max_position:.2f}",
            symbol=symbol,
            max_notional=max_notional,
            max_position=max_position,
        )
