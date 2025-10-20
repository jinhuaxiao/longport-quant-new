"""Example strategy that logs incoming quotes."""

from __future__ import annotations

from loguru import logger

from longport_quant.execution.order_router import OrderRouter
from longport_quant.portfolio.state import PortfolioService
from longport_quant.common.types import Signal, StrategyBase


class SampleStrategy(StrategyBase):
    @classmethod
    async def create(
        cls,
        order_router: OrderRouter,
        portfolio: PortfolioService,
        risk_engine=None,
        signal_dispatcher=None,
    ) -> "SampleStrategy":
        return cls(order_router, portfolio, risk_engine, signal_dispatcher)

    async def on_start(self) -> None:
        logger.info("SampleStrategy started")

    async def on_stop(self) -> None:
        logger.info("SampleStrategy stopped")

    async def on_quote(self, quote: dict) -> None:
        symbol = quote.get("symbol")
        price = quote.get("price")
        logger.debug("SampleStrategy received quote {} @ {}", symbol, price)
        # Insert signal generation logic here.
        if symbol and price:
            signal = Signal(symbol=symbol, side="BUY", quantity=1, price=price)
            await self.dispatch(signal)
