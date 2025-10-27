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
        """清理资源，关闭连接"""
        if self._trade_ctx is not None:
            try:
                # 尝试取消订单订阅（如果有的话）
                try:
                    await asyncio.to_thread(self._trade_ctx.unsubscribe)
                    logger.debug("Unsubscribed from order updates")
                except Exception as e:
                    logger.debug(f"Failed to unsubscribe from orders: {e}")

                # 强制删除对象，触发底层资源清理
                ctx_to_delete = self._trade_ctx
                self._trade_ctx = None
                del ctx_to_delete

                # 建议垃圾回收器立即回收
                import gc
                gc.collect()

                logger.debug("TradeContext cleaned up and resources released")
            except Exception as e:
                logger.warning(f"Error during TradeContext cleanup: {e}")
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

    async def submit_conditional_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        trigger_price: float,
        limit_price: float | None = None,
        remark: str | None = None
    ) -> Dict[str, Any]:
        """
        提交LIT条件单（Limit If Touched - 到价止盈止损）

        Args:
            symbol: 标的代码 (e.g., "1398.HK", "NVDA.US")
            side: 买卖方向 ("BUY" or "SELL")
            quantity: 数量
            trigger_price: 触发价格（当市价达到此价格时触发订单）
            limit_price: 限价价格（触发后以此价格下限价单），如果为None则使用trigger_price
            remark: 备注

        Returns:
            包含order_id的字典

        Example:
            # 止损单：当价格跌到100时，以99.5卖出
            await client.submit_conditional_order(
                symbol="1398.HK",
                side="SELL",
                quantity=1000,
                trigger_price=100.0,
                limit_price=99.5,
                remark="Stop Loss Backup"
            )

            # 止盈单：当价格涨到120时，以120卖出
            await client.submit_conditional_order(
                symbol="1398.HK",
                side="SELL",
                quantity=1000,
                trigger_price=120.0,
                limit_price=120.0,
                remark="Take Profit Backup"
            )
        """
        ctx = await self._ensure_context()
        try:
            # 如果没有指定limit_price，使用trigger_price
            if limit_price is None:
                limit_price = trigger_price

            response = await asyncio.to_thread(
                ctx.submit_order,
                symbol,
                openapi.OrderType.LIT,  # Limit If Touched
                self._resolve_side(side),
                Decimal(str(quantity)),
                openapi.TimeInForceType.GoodTilCanceled,  # GTC - 订单一直有效直到取消
                self._resolve_price(limit_price),  # 触发后的限价
                trigger_price=self._resolve_price(trigger_price),  # 触发价格
                remark=remark or f"Conditional Order - {side}"
            )

            payload = {"order_id": response.order_id}
            logger.info(
                f"✅ 条件单已提交: {symbol} {side} {quantity}股, "
                f"触发价=${trigger_price:.2f}, 限价=${limit_price:.2f}, "
                f"订单ID={response.order_id}"
            )
            return payload

        except OpenApiException as exc:
            logger.error(f"❌ 提交条件单失败: {exc}")
            raise

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

    async def replace_order(
        self,
        order_id: str,
        quantity: int,
        price: float | None = None,
        trigger_price: float | None = None,
        limit_offset: float | None = None,
        trailing_amount: float | None = None,
        trailing_percent: float | None = None,
        remark: str | None = None,
    ) -> Dict[str, Any]:
        ctx = await self._ensure_context()
        try:
            await asyncio.to_thread(
                ctx.replace_order,
                order_id,
                quantity,
                self._resolve_price(price) if price is not None else None,
                self._resolve_price(trigger_price) if trigger_price is not None else None,
                self._resolve_price(limit_offset) if limit_offset is not None else None,
                self._resolve_price(trailing_amount) if trailing_amount is not None else None,
                self._resolve_price(trailing_percent) if trailing_percent is not None else None,
                remark,
            )
        except OpenApiException as exc:  # pragma: no cover - network errors
            logger.error("Trade API replace order failed: {}", exc)
            raise
        return {"order_id": order_id, "status": "replaced"}

    async def set_on_order_changed(self, callback) -> None:
        ctx = await self._ensure_context()
        await asyncio.to_thread(ctx.set_on_order_changed, callback)

    async def subscribe_orders(self) -> None:
        ctx = await self._ensure_context()
        await asyncio.to_thread(ctx.subscribe)

    async def unsubscribe_orders(self) -> None:
        ctx = await self._ensure_context()
        await asyncio.to_thread(ctx.unsubscribe)

    async def _ensure_context(self) -> openapi.TradeContext:
        async with self._context_lock:
            if self._trade_ctx is None:
                logger.info("Initialising Longport TradeContext")
                self._trade_ctx = await asyncio.to_thread(openapi.TradeContext, self._config)
        return self._trade_ctx

    async def get_trade_context(self) -> openapi.TradeContext:
        return await self._ensure_context()

    async def get_account(self) -> Dict[str, Any]:
        """
        获取账户信息的便捷方法

        Returns:
            包含cash, buy_power, net_assets, positions等信息的字典
        """
        try:
            balances = await self.account_balance()
            positions_resp = await self.stock_positions()

            cash = {}
            buy_power = {}
            net_assets = {}
            remaining_finance = {}  # 剩余融资额度

            # 先收集所有币种（从 cash_infos）
            all_currencies = set()
            for balance in balances:
                if hasattr(balance, 'cash_infos') and balance.cash_infos:
                    for cash_info in balance.cash_infos:
                        all_currencies.add(cash_info.currency)

            # 为每个币种获取详细信息
            for currency in all_currencies:
                # 获取该币种的购买力和净资产
                currency_balance = await self.account_balance(currency)
                if currency_balance and len(currency_balance) > 0:
                    balance = currency_balance[0]

                    # 获取购买力（仅供参考，不作为下单依据）
                    buy_power[currency] = float(balance.buy_power) if hasattr(balance, 'buy_power') else 0

                    # 获取净资产
                    net_assets[currency] = float(balance.net_assets) if hasattr(balance, 'net_assets') else 0

                    # 获取详细现金信息
                    actual_cash = 0
                    withdraw_cash_amount = 0
                    frozen_cash_amount = 0

                    if hasattr(balance, 'cash_infos') and balance.cash_infos:
                        for cash_info in balance.cash_infos:
                            if cash_info.currency == currency:
                                # 可用现金（可能未扣除挂单冻结）
                                actual_cash = float(cash_info.available_cash)

                                # 获取可提现金（已扣除所有冻结，最准确）
                                if hasattr(cash_info, 'withdraw_cash'):
                                    withdraw_cash_amount = float(cash_info.withdraw_cash)

                                # 获取冻结资金（挂单占用）
                                if hasattr(cash_info, 'frozen_cash'):
                                    frozen_cash_amount = float(cash_info.frozen_cash)
                                break

                    # 获取融资额度信息
                    remaining_finance_amt = 0
                    if hasattr(balance, 'remaining_finance_amount'):
                        remaining_finance_amt = float(balance.remaining_finance_amount)
                        remaining_finance[currency] = remaining_finance_amt

                    # 选择合适的可用资金
                    # 1. 如果 available_cash 为负数（使用了融资），则使用 buy_power
                    # 2. 否则使用 available_cash（已扣除冻结资金）
                    if actual_cash < 0:
                        # 账户使用了融资，现金为负数，使用购买力
                        cash[currency] = buy_power[currency]
                        logger.debug(
                            f"{currency} 账户使用融资: "
                            f"欠款=${actual_cash:,.2f}, "
                            f"购买力=${buy_power[currency]:,.2f}"
                        )
                    else:
                        # 正常情况，使用可用现金
                        cash[currency] = actual_cash

                    # 记录资金详情（用于调试）
                    if withdraw_cash_amount != actual_cash or frozen_cash_amount > 0:
                        logger.debug(
                            f"{currency} 资金详情: "
                            f"可用=${actual_cash:,.2f}, "
                            f"可提=${withdraw_cash_amount:,.2f}, "
                            f"冻结=${frozen_cash_amount:,.2f}, "
                            f"购买力=${buy_power[currency]:,.2f}"
                        )

                    # 注意：不再使用 buy_power 作为可用资金，因为它可能包含已用完的融资额度
                    # 如果需要使用融资，应该单独判断 remaining_finance 是否足够

            # 获取持仓信息
            positions = []
            for channel in positions_resp.channels:
                for pos in channel.positions:
                    positions.append({
                        "symbol": pos.symbol,
                        "quantity": pos.quantity,
                        "available_quantity": pos.available_quantity,
                        "cost_price": float(pos.cost_price) if pos.cost_price else 0,
                        "currency": pos.currency,
                        "market": pos.market
                    })

            return {
                "account_id": "",  # LongPort API不直接提供account_id
                "cash": cash,
                "buy_power": buy_power,
                "net_assets": net_assets,
                "remaining_finance": remaining_finance,  # 剩余可用融资额度
                "positions": positions,
                "position_count": len(positions)
            }

        except Exception as e:
            logger.error(f"获取账户信息失败: {e}")
            return {
                "account_id": "",
                "cash": {"HKD": 0, "USD": 0},
                "buy_power": {"HKD": 0, "USD": 0},
                "net_assets": {"HKD": 0, "USD": 0},
                "remaining_finance": {"HKD": 0, "USD": 0},
                "positions": [],
                "position_count": 0
            }

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
        return openapi.OrderType.LO if price is not None else openapi.OrderType.MO  # MO = Market Order


__all__ = ["LongportTradingClient"]
