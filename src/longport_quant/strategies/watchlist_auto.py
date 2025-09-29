"""Strategy that auto-trades the configured watchlist."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from loguru import logger

from longport_quant.config import get_settings
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.execution.order_router import OrderRouter
from longport_quant.portfolio.state import PortfolioService
from longport_quant.strategy.base import Signal, StrategyBase


class AutoTradeStrategy(StrategyBase):
    """Submit basic buy orders for every symbol in the watchlist when trading opens."""

    def __init__(
        self,
        order_router: OrderRouter,
        portfolio: PortfolioService,
        risk_engine=None,
        signal_dispatcher=None,
    ) -> None:
        super().__init__(order_router, portfolio, risk_engine, signal_dispatcher)
        self._settings = get_settings()
        self._watchlist = WatchlistLoader().load()
        self._executed: set[str] = set()
        self._daily_executions: dict[str, datetime] = {}
        self._market_windows = self._build_market_windows()
        self._market_lookup = {item.symbol: item.market.lower() for item in self._watchlist}

    @classmethod
    async def create(
        cls,
        order_router: OrderRouter,
        portfolio: PortfolioService,
        risk_engine=None,
        signal_dispatcher=None,
    ) -> "AutoTradeStrategy":
        return cls(order_router, portfolio, risk_engine, signal_dispatcher)

    async def on_start(self) -> None:
        logger.info(
            "AutoTradeStrategy starting with {} symbols", len(self._watchlist.items)
        )
        if self._risk_engine:
            self._prime_risk_limits()

    async def on_quote(self, quote: dict) -> None:
        symbol = quote.get("symbol")
        price_raw = quote.get("price")
        if symbol is None or price_raw is None:
            return

        try:
            price = float(price_raw)
        except (TypeError, ValueError):
            return

        if price <= 0:
            return

        if symbol in self._executed:
            if self._is_new_trading_day(symbol):
                self._executed.discard(symbol)
            else:
                return

        market = self._resolve_market(symbol)
        if market is None:
            return

        if not self._in_trading_window(market):
            return

        quantity = self._calculate_quantity(market, price)
        if quantity <= 0:
            return

        signal = Signal(symbol=symbol, side="BUY", quantity=quantity, price=price)
        try:
            await self.dispatch(signal)
        except Exception as exc:  # noqa: BLE001 - log and continue
            logger.error("Dispatch failed for {}: {}", symbol, exc)
            return

        logger.info(
            "Submitted BUY {} x {} @ {price:.2f}",
            symbol,
            quantity,
            price=price,
        )
        self._executed.add(symbol)
        self._daily_executions[symbol] = datetime.now(ZoneInfo(self._settings.timezone))

    async def on_stop(self) -> None:
        logger.info("AutoTradeStrategy stopped")

    def _build_market_windows(self) -> dict[str, list[tuple[time, time]]]:
        tz = ZoneInfo(self._settings.timezone)
        return {
            "hk": [(time(9, 30, tzinfo=tz), time(12, 0, tzinfo=tz)), (time(13, 0, tzinfo=tz), time(16, 0, tzinfo=tz))],
            "sz": [(time(9, 30, tzinfo=tz), time(11, 30, tzinfo=tz)), (time(13, 0, tzinfo=tz), time(15, 0, tzinfo=tz))],
            "us": [(time(21, 30, tzinfo=tz), time(4, 0, tzinfo=tz))],  # 21:30-04:00 local (UTC+8)
        }

    def _resolve_market(self, symbol: str) -> str | None:
        return self._market_lookup.get(symbol)

    def _in_trading_window(self, market: str) -> bool:
        now = datetime.now(ZoneInfo(self._settings.timezone)).time()
        windows = self._market_windows.get(market)
        if not windows:
            return False

        for start, end in windows:
            if start <= end and start <= now <= end:
                return True
            if start > end:  # Overnight session e.g. US market
                if now >= start or now <= end:
                    return True
        return False

    def _calculate_quantity(self, market: str, price: float) -> int:
        budgets = {"hk": 20000.0, "sz": 10000.0, "us": 5000.0}
        budget = budgets.get(market, 0.0)
        quantity = int(budget // price)
        return max(quantity, 0)

    def _is_new_trading_day(self, symbol: str) -> bool:
        last_exec = self._daily_executions.get(symbol)
        if not last_exec:
            return True
        now = datetime.now(ZoneInfo(self._settings.timezone))
        return now.date() > last_exec.date()

    def _prime_risk_limits(self) -> None:
        assert self._risk_engine is not None
        for item in self._watchlist:
            notional = 20000.0 if item.market.lower() == "hk" else 5000.0
            if item.market.lower() == "sz":
                notional = 10000.0
            self._risk_engine.set_limit(item.symbol, max_notional=notional, max_position=notional)
