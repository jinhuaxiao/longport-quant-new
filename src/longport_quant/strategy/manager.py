"""Strategy lifecycle orchestration."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from importlib import import_module
from typing import Optional

from loguru import logger

from longport_quant.config.settings import Settings
from longport_quant.data.market_data_service import MarketDataService, QuoteHandler
from longport_quant.execution.order_router import OrderRouter
from longport_quant.portfolio.state import PortfolioService
from longport_quant.risk.checks import RiskEngine
from longport_quant.strategy.base import StrategyBase
from longport_quant.strategy.dispatcher import SignalDispatcher
from longport_quant.notifications import SlackNotifier


class StrategyManager(AbstractAsyncContextManager):
    def __init__(
        self,
        settings: Settings,
        market_data: MarketDataService,
        order_router: OrderRouter,
        risk_engine: RiskEngine,
        portfolio: PortfolioService,
        slack_notifier: SlackNotifier | None = None,
    ) -> None:
        self._settings = settings
        self._market_data = market_data
        self._order_router = order_router
        self._risk_engine = risk_engine
        self._portfolio = portfolio
        self._strategies: list[StrategyBase] = []
        self._quote_handler: Optional[QuoteHandler] = None
        self._signal_dispatcher = SignalDispatcher(order_router, slack_notifier)

    async def __aenter__(self) -> "StrategyManager":
        await self._portfolio.refresh()
        await self._load_strategies()

        async def handler(quote: dict) -> None:
            await asyncio.gather(*(strategy.on_quote(quote) for strategy in self._strategies))

        self._quote_handler = handler
        self._market_data.subscribe(handler)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> Optional[bool]:
        if self._quote_handler:
            self._market_data.unsubscribe(self._quote_handler)
        await asyncio.gather(*(strategy.on_stop() for strategy in self._strategies), return_exceptions=True)
        return None

    async def _load_strategies(self) -> None:
        if not self._settings.strategy_modules:
            logger.warning("No strategy modules configured")
            return

        for dotted_path in self._settings.strategy_modules:
            module_name, class_name = dotted_path.rsplit(".", maxsplit=1)
            module = import_module(module_name)
            strategy_cls = getattr(module, class_name)
            strategy: StrategyBase = await strategy_cls.create(
                self._order_router,
                self._portfolio,
                self._risk_engine,
                self._signal_dispatcher,
            )
            self._strategies.append(strategy)
            await strategy.on_start()
            logger.info("Loaded strategy {}", dotted_path)
