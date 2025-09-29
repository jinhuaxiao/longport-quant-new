"""Market data subscription and routing via Longport SDK."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Awaitable, Callable, Optional

from loguru import logger
from longport import OpenApiException, openapi

from longport_quant.config.sdk import build_sdk_config
from longport_quant.config.settings import Settings
from longport_quant.data.watchlist import Watchlist, WatchlistLoader
from longport_quant.utils.events import EventBus


QuoteHandler = Callable[[dict], Awaitable[None]]


class MarketDataService:
    """Handles Longport market data subscriptions for a curated watchlist."""

    def __init__(self, settings: Settings, config: openapi.Config | None = None) -> None:
        self._settings = settings
        self._config = config
        self._event_bus = EventBus()
        self._watchlist_loader = WatchlistLoader()
        self._watchlist: Watchlist | None = None
        self._quote_ctx: openapi.QuoteContext | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    async def __aenter__(self) -> "MarketDataService":
        self._loop = asyncio.get_running_loop()
        self._watchlist = self._watchlist_loader.load()
        logger.info(
            "Market data service starting for {} symbols", len(self._watchlist.items)
        )
        if not self._watchlist.items:
            logger.warning("Watchlist is empty; no quotes will be streamed")

        config = self._config or build_sdk_config(self._settings)
        self._config = config
        try:
            self._quote_ctx = await asyncio.to_thread(openapi.QuoteContext, config)
        except OpenApiException as exc:  # pragma: no cover - network errors
            logger.error("Failed to initialise QuoteContext: {}", exc)
            raise

        self._quote_ctx.set_on_quote(self._handle_quote)  # type: ignore[arg-type]
        self._running = True

        if self._watchlist.items:
            await self._subscribe_symbols(self._watchlist.symbols())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> Optional[bool]:
        self._running = False
        if self._quote_ctx and self._watchlist and self._watchlist.items:
            try:
                await asyncio.to_thread(
                    self._quote_ctx.unsubscribe,
                    self._watchlist.symbols(),
                    [openapi.SubType.Quote],
                )
            except OpenApiException as exc:  # pragma: no cover - network errors
                logger.warning("Quote unsubscribe failed: {}", exc)
        self._quote_ctx = None
        return None

    async def _subscribe_symbols(self, symbols: list[str]) -> None:
        if not self._quote_ctx or not symbols:
            return
        logger.info(
            "Subscribing to {} symbols via QuoteContext in one batch", len(symbols)
        )
        try:
            await asyncio.to_thread(
                self._quote_ctx.subscribe,
                symbols,
                [openapi.SubType.Quote],
                True,
            )
        except OpenApiException as exc:  # pragma: no cover - network errors
            logger.error("Quote subscription failed: {}", exc)
            raise

    def _handle_quote(self, symbol: str, event: openapi.PushQuote) -> None:
        if not self._running or not self._loop:
            return

        price = event.last_done
        try:
            price_value = float(price) if price is not None else None
        except (TypeError, ValueError):
            price_value = None

        payload = {
            "symbol": symbol,
            "price": price_value,
            "raw": event,
            "timestamp": getattr(event, "timestamp", None),
        }

        future = asyncio.run_coroutine_threadsafe(
            self._event_bus.publish("quote", payload),
            self._loop,
        )
        future.add_done_callback(partial(self._log_future_error, symbol=symbol))

    @staticmethod
    def _log_future_error(future: asyncio.Future, *, symbol: str) -> None:
        if exc := future.exception():  # pragma: no cover - logging helper
            logger.error("Quote handler for {} raised: {}", symbol, exc)

    def subscribe(self, handler: QuoteHandler) -> None:
        """Register a callback for quotes."""

        self._event_bus.subscribe("quote", handler)

    def unsubscribe(self, handler: QuoteHandler) -> None:
        self._event_bus.unsubscribe("quote", handler)


__all__ = ["MarketDataService", "QuoteHandler"]
