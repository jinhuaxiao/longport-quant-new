"""High level order router handling pre-trade checks and submission."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Optional

from loguru import logger
from longport import openapi

from longport_quant.config.settings import Settings
from longport_quant.execution.client import LongportTradingClient
from longport_quant.risk.checks import RiskEngine


class OrderRouter(AbstractAsyncContextManager):
    def __init__(
        self,
        settings: Settings,
        config: openapi.Config | None = None,
        risk_engine: RiskEngine | None = None,
    ) -> None:
        self._settings = settings
        self._client = LongportTradingClient(settings, config)
        self._risk_engine = risk_engine

    async def __aenter__(self) -> "OrderRouter":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> Optional[bool]:
        await self._client.__aexit__(exc_type, exc, tb)
        return None

    def bind_risk_engine(self, risk_engine: RiskEngine) -> None:
        self._risk_engine = risk_engine

    @property
    def trading_client(self) -> LongportTradingClient:
        return self._client

    async def get_trade_context(self) -> openapi.TradeContext:
        return await self._client.get_trade_context()

    async def _get_pending_sell_quantity(self, symbol: str) -> float:
        """
        Get the quantity occupied by pending sell orders.

        Args:
            symbol: Symbol to query

        Returns:
            Total quantity in pending sell orders
        """
        try:
            trade_context = await self._client.get_trade_context()
            orders = await trade_context.today_orders()

            pending_qty = 0.0
            for order in orders:
                if (order.symbol == symbol and
                    order.side in ["Sell", "SELL"] and
                    order.status in ["NotReported", "ReplacedNotReported", "ProtectedNotReported",
                                    "VarietiesNotReported", "Filled", "WaitToNew", "New",
                                    "WaitToReplace", "PendingReplace", "Replaced", "PartialFilled",
                                    "WaitToCancel"]):
                    # Include all non-terminal order states
                    pending_qty += float(order.quantity - order.executed_quantity)

            return pending_qty
        except Exception as e:
            logger.warning(f"Failed to get pending sell orders for {symbol}: {e}")
            return 0.0

    async def _get_available_quantity(self, symbol: str) -> float:
        """
        Get true available quantity for selling, considering pending orders.

        Args:
            symbol: Symbol to query

        Returns:
            Available quantity (total - pending sell orders)
        """
        if not self._risk_engine:
            logger.warning("No risk engine available for position check")
            return 0.0

        try:
            # Get total position from portfolio
            portfolio = self._risk_engine._portfolio
            position = await portfolio.get_position(symbol)

            if not position:
                logger.debug(f"  ℹ️ {symbol}: No position found")
                return 0.0

            total_qty = position.quantity if position.quantity > 0 else 0.0

            # Get quantity occupied by pending sell orders
            pending_qty = await self._get_pending_sell_quantity(symbol)

            # Calculate true available quantity
            available_qty = max(0.0, total_qty - pending_qty)

            logger.info(
                f"  📊 {symbol} 持仓检查: 总持仓={total_qty}, "
                f"pending卖单={pending_qty}, 可用={available_qty}"
            )

            return available_qty

        except Exception as e:
            logger.error(f"Failed to get available quantity for {symbol}: {e}")
            return 0.0

    async def submit(self, order: dict) -> dict:
        # Validate with risk engine (fix: add await for async method)
        if self._risk_engine:
            is_valid, error_msg = await self._risk_engine.validate_order(order)
            if not is_valid:
                logger.warning("Order blocked by risk engine: {} - {}", order, error_msg)
                raise ValueError(f"Order did not pass risk checks: {error_msg}")

        # Pre-check for SELL orders: prevent short selling
        side = (order.get("side") or "").upper()
        if side in {"SELL", "S"}:
            symbol = order["symbol"]
            sell_qty = int(order.get("quantity", 0) or 0)
            if sell_qty <= 0:
                raise ValueError("Invalid sell quantity")

            # Check available position
            available_qty = await self._get_available_quantity(symbol)

            logger.debug(
                "Sell order check for {}: quantity={}, available={}",
                symbol,
                sell_qty,
                available_qty,
            )

            if sell_qty > available_qty:
                error_msg = (
                    f"Sell quantity {sell_qty} exceeds available position {available_qty} "
                    f"for {symbol}. Short selling is not allowed."
                )
                logger.warning(error_msg)
                raise ValueError(error_msg)

        # Pre-check using broker estimate for BUY limit orders
        try:
            price = order.get("price")
            if side in {"BUY", "B"} and price is not None:
                symbol = order["symbol"]
                qty = int(order.get("quantity", 0) or 0)
                if qty <= 0:
                    raise ValueError("Invalid order quantity")

                resp = await self._client.estimate_max_purchase_quantity(
                    symbol=symbol,
                    order_type=openapi.OrderType.LO,
                    side=openapi.OrderSide.Buy,
                    price=float(price),
                )

                cash_max = int(getattr(resp, "cash_max_qty", 0) or 0)
                margin_max = int(getattr(resp, "margin_max_qty", 0) or 0)
                allow_max = max(cash_max, margin_max)

                logger.debug(
                    "Estimate buy limit for {} @ {}: cash={}, margin={}, max={}",
                    symbol,
                    price,
                    cash_max,
                    margin_max,
                    allow_max,
                )

                if allow_max <= 0 or qty > allow_max:
                    raise ValueError(f"Buy quantity {qty} exceeds limit {allow_max}")
        except Exception as e:
            # If estimate fails, continue submission but warn
            logger.warning("Pre-check estimate failed, continue to submit: {}", e)

        return await self._client.submit_order(order)

    async def cancel(self, order_id: str) -> dict:
        return await self._client.cancel_order(order_id)
