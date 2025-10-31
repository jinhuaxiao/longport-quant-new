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

    async def submit(self, order: dict) -> dict:
        if self._risk_engine and not self._risk_engine.validate_order(order):
            logger.warning("Order blocked by risk engine: {}", order)
            raise ValueError("Order did not pass risk checks")

        # Pre-check using broker estimate for BUY limit orders
        try:
            side = (order.get("side") or "").upper()
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
