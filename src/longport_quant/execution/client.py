"""Longport SDK trade client wrapper."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from longport import OpenApiException, openapi

from longport_quant.config.settings import Settings
from longport_quant.config.sdk import build_sdk_config


class LongportTradingClient:
    """Asynchronous-friendly wrapper around the Longport TradeContext."""

    def __init__(self, settings: Settings, config: openapi.Config | None = None) -> None:
        self._settings = settings
        self._config = config or build_sdk_config(settings)
        self._trade_ctx: openapi.TradeContext | None = None
        self._context_lock = asyncio.Lock()

    async def __aenter__(self) -> "LongportTradingClient":
        await self._ensure_context()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # SDK context does not expose explicit close semantics; rely on GC.
        self._trade_ctx = None

    async def submit_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        ctx = await self._ensure_context()
        try:
            response = await asyncio.to_thread(
                ctx.submit_order,
                order["symbol"],
                self._resolve_order_type(order),
                self._resolve_side(order.get("side")),
                Decimal(str(order.get("quantity"))),
                openapi.TimeInForceType.Day,
                self._resolve_price(order.get("price")),
            )
        except OpenApiException as exc:  # pragma: no cover - network errors
            logger.error("Trade API submit failed: {}", exc)
            raise

        payload = {"order_id": response.order_id}
        logger.debug("Submit order response: {}", payload)
        return payload

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        ctx = await self._ensure_context()
        try:
            await asyncio.to_thread(ctx.cancel_order, order_id)
        except OpenApiException as exc:  # pragma: no cover - network errors
            logger.error("Trade API cancel failed: {}", exc)
            raise
        return {"order_id": order_id, "status": "cancelled"}

    async def account_balance(self, currency: str | None = None) -> List[openapi.AccountBalance]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.account_balance, currency)

    async def cash_flow(
        self,
        start_at: datetime,
        end_at: datetime,
        **kwargs: Any,
    ) -> List[openapi.CashFlow]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.cash_flow, start_at, end_at, **kwargs)

    async def fund_positions(
        self,
        symbols: Optional[List[str]] = None,
    ) -> openapi.FundPositionsResponse:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.fund_positions, symbols)

    async def stock_positions(
        self,
        symbols: Optional[List[str]] = None,
    ) -> openapi.StockPositionsResponse:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.stock_positions, symbols)

    async def margin_ratio(self, symbol: str) -> openapi.MarginRatio:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.margin_ratio, symbol)

    async def estimate_max_purchase_quantity(
        self,
        symbol: str,
        order_type: openapi.OrderType,
        side: openapi.OrderSide,
        price: float,
        currency: str | None = None,
        order_id: str | None = None,
        fractional_shares: bool = False,
    ) -> openapi.EstimateMaxPurchaseQuantityResponse:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(
            ctx.estimate_max_purchase_quantity,
            symbol,
            order_type,
            side,
            price,
            currency,
            order_id,
            fractional_shares,
        )

    async def history_orders(
        self,
        symbol: str | None = None,
        status: openapi.OrderStatus | None = None,
        side: openapi.OrderSide | None = None,
        market: openapi.Market | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> List[openapi.OrderHistoryDetail]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(
            ctx.history_orders,
            symbol,
            status,
            side,
            market,
            start_at,
            end_at,
        )

    async def today_orders(
        self,
        symbol: str | None = None,
        status: openapi.OrderStatus | None = None,
        side: openapi.OrderSide | None = None,
        market: openapi.Market | None = None,
        order_id: str | None = None,
    ) -> List[openapi.OrderDetail]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(
            ctx.today_orders,
            symbol,
            status,
            side,
            market,
            order_id,
        )

    async def order_detail(self, order_id: str) -> openapi.OrderDetail:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.order_detail, order_id)

    async def history_executions(
        self,
        symbol: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> List[openapi.Execution]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.history_executions, symbol, start_at, end_at)

    async def today_executions(
        self,
        symbol: str | None = None,
        order_id: str | None = None,
    ) -> List[openapi.Execution]:
        ctx = await self._ensure_context()
        return await asyncio.to_thread(ctx.today_executions, symbol, order_id)

    async def _ensure_context(self) -> openapi.TradeContext:
        async with self._context_lock:
            if self._trade_ctx is None:
                logger.info("Initialising Longport TradeContext")
                self._trade_ctx = await asyncio.to_thread(openapi.TradeContext, self._config)
        return self._trade_ctx

    def _resolve_side(self, side: str | None) -> openapi.OrderSide:
        if not side:
            raise ValueError("Order side not provided")
        side_upper = side.upper()
        if side_upper in {"BUY", "B"}:
            return openapi.OrderSide.Buy
        if side_upper in {"SELL", "S"}:
            return openapi.OrderSide.Sell
        raise ValueError(f"Unsupported order side: {side}")

    def _resolve_price(self, price: Any) -> Decimal | None:
        if price is None:
            return None
        return Decimal(str(price))

    def _resolve_order_type(self, order: Dict[str, Any]) -> openapi.OrderType:
        price = order.get("price")
        return openapi.OrderType.LO if price is not None else openapi.OrderType.Market


__all__ = ["LongportTradingClient"]
