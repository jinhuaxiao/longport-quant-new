"""Strategy base classes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

from typing import TYPE_CHECKING

from longport_quant.execution.order_router import OrderRouter
from longport_quant.portfolio.state import PortfolioService
from longport_quant.risk.checks import RiskEngine

if TYPE_CHECKING:
    from longport_quant.strategy.dispatcher import SignalDispatcher


@dataclass
class Signal:
    symbol: str
    side: str
    quantity: float
    price: float


class StrategyBase(ABC):
    def __init__(
        self,
        order_router: OrderRouter,
        portfolio: PortfolioService,
        risk_engine: RiskEngine | None = None,
        signal_dispatcher: "SignalDispatcher" | None = None,
    ) -> None:
        self._order_router = order_router
        self._portfolio = portfolio
        self._risk_engine = risk_engine
        self._signal_dispatcher = signal_dispatcher

    async def on_start(self) -> None:
        """Hook called when the strategy starts."""

    async def on_stop(self) -> None:
        """Hook called when the strategy stops."""

    @abstractmethod
    async def on_quote(self, quote: dict) -> None:
        """Consume a quote and optionally generate orders."""

    async def dispatch(self, signal: Signal) -> None:
        if self._signal_dispatcher:
            await self._signal_dispatcher.dispatch(signal)
            return

        order = {
            "symbol": signal.symbol,
            "side": signal.side,
            "quantity": signal.quantity,
            "price": signal.price,
        }
        await self._order_router.submit(order)

    @classmethod
    @abstractmethod
    async def create(
        cls,
        order_router: OrderRouter,
        portfolio: PortfolioService,
        risk_engine: RiskEngine | None = None,
        signal_dispatcher: "SignalDispatcher" | None = None,
    ) -> "StrategyBase":
        """Factory returning an instance of the strategy."""


StrategyFactory = AsyncIterator[StrategyBase]
