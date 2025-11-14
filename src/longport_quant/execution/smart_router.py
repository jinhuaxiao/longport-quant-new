"""Smart order routing and execution system."""

from __future__ import annotations

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import asyncio
import math

from loguru import logger
from longport.openapi import TradeContext, OrderSide, OrderType, TimeInForceType
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import OrderRecord, FillRecord, RealtimeQuote
from longport_quant.common.types import Signal
from sqlalchemy import select, and_
from sqlalchemy.dialects.postgresql import insert


class ExecutionStrategy(Enum):
    """Order execution strategies."""
    AGGRESSIVE = "aggressive"  # Market orders, immediate execution
    PASSIVE = "passive"  # Limit orders at favorable prices
    ADAPTIVE = "adaptive"  # Mix based on market conditions
    ICEBERG = "iceberg"  # Hide large order size
    TWAP = "twap"  # Time-weighted average price
    VWAP = "vwap"  # Volume-weighted average price


@dataclass
class OrderRequest:
    """Order request details."""
    symbol: str
    side: str  # BUY or SELL
    quantity: int
    order_type: str  # MARKET or LIMIT
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = "DAY"
    strategy: ExecutionStrategy = ExecutionStrategy.ADAPTIVE
    urgency: int = 5  # 1-10, 10 being most urgent
    max_slippage: float = 0.005  # 0.5%
    signal: Optional[Signal] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderSlice:
    """Represents a slice of a larger order."""
    parent_id: str
    slice_number: int
    quantity: int
    order_type: str
    limit_price: Optional[float] = None
    scheduled_time: Optional[datetime] = None
    executed: bool = False
    order_id: Optional[str] = None


@dataclass
class ExecutionResult:
    """Order execution result."""
    success: bool
    order_id: Optional[str] = None
    filled_quantity: int = 0
    average_price: float = 0.0
    slippage: float = 0.0
    commission: float = 0.0
    execution_time: Optional[datetime] = None
    error_message: Optional[str] = None
    child_orders: List[str] = field(default_factory=list)


class SmartOrderRouter:
    """Smart order routing and execution engine."""

    def __init__(
        self,
        trade_context: TradeContext,
        db: DatabaseSessionManager,
        quote_client = None,
        settings = None
    ):
        """
        Initialize smart order router.

        Args:
            trade_context: LongPort trade context
            db: Database session manager
            quote_client: Optional QuoteDataClient for fetching tick size info
            settings: Optional Settings instance for safety controls
        """
        self.trade_context = trade_context
        self.db = db
        self.quote_client = quote_client
        self._settings = settings  # Fixed: use _settings to match usage in code
        self._active_orders: Dict[str, OrderRequest] = {}
        self._order_slices: Dict[str, List[OrderSlice]] = {}
        self._market_data_cache: Dict[str, Dict] = {}
        self._tick_size_cache: Dict[str, float] = {}  # Cache for tick sizes from API
        self._lot_size_cache: Dict[str, int] = {}  # Cache for lot sizes (board lots)

    def _round_price_to_tick(self, symbol: str, price: float) -> float:
        """
        将价格舍入到有效的tick size

        Args:
            symbol: 股票代码
            price: 原始价格

        Returns:
            符合tick size规则的价格
        """
        if ".US" in symbol:
            # 美股：通常是0.01
            tick_size = 0.01
            decimal_places = 2
        else:
            # 港股tick size规则
            if price < 0.01:
                tick_size = 0.001
                decimal_places = 3
            elif price < 0.25:
                tick_size = 0.001
                decimal_places = 3
            elif price < 0.50:
                tick_size = 0.005
                decimal_places = 3
            elif price < 10.00:
                tick_size = 0.01
                decimal_places = 2
            elif price < 20.00:
                tick_size = 0.02
                decimal_places = 2
            elif price < 100.00:
                tick_size = 0.05
                decimal_places = 2
            elif price < 200.00:
                tick_size = 0.10
                decimal_places = 2  # 改为2位，因为0.10精度是2位小数
            elif price < 500.00:
                tick_size = 0.20
                decimal_places = 2  # 改为2位，因为0.20精度是2位小数
            elif price < 1000.00:
                tick_size = 0.50
                decimal_places = 2  # 改为2位，因为0.50精度是2位小数
            elif price < 2000.00:
                tick_size = 1.00
                decimal_places = 0  # 整数
            elif price < 5000.00:
                tick_size = 2.00
                decimal_places = 0  # 整数
            else:
                tick_size = 5.00
                decimal_places = 0  # 整数

        # 舍入到最接近的tick
        rounded = round(price / tick_size) * tick_size
        # 根据tick size确定合适的小数位数
        result = round(rounded, decimal_places)

        # 打印tick size调整详情
        if abs(result - price) > 0.0001:
            logger.debug(
                f"  🎯 Tick Size调整: {symbol} ${price:.4f} → ${result:.{decimal_places}f} "
                f"(tick_size={tick_size}, 小数位={decimal_places})"
            )

        return result

    async def _get_lot_size(self, symbol: str) -> int:
        """
        获取股票的手数（买卖单位/Board Lot）

        查询优先级: 缓存 > API > 数据库 > 默认值

        Args:
            symbol: 股票代码

        Returns:
            手数（每手股数）
        """
        # 如果已缓存，直接返回
        if symbol in self._lot_size_cache:
            return self._lot_size_cache[symbol]

        lot_size = None

        # 尝试从API获取
        if self.quote_client:
            try:
                static_info = await self.quote_client.get_static_info([symbol])
                if static_info and len(static_info) > 0:
                    info = static_info[0]

                    # 🔥 修复：尝试多个可能的属性名
                    for attr_name in ['board_lot', 'lot_size', 'boardLot']:
                        lot_size = getattr(info, attr_name, None)
                        if lot_size and lot_size > 0:
                            self._lot_size_cache[symbol] = lot_size
                            logger.info(f"  📊 {symbol} 手数(API): {lot_size}股/手")
                            return lot_size

                    # 所有属性都无效
                    logger.warning(
                        f"⚠️ {symbol} API返回但board_lot/lot_size无效，可用属性: "
                        f"{[(k,v) for k,v in vars(info).items() if 'lot' in k.lower()]}"
                    )
                else:
                    logger.warning(f"⚠️ {symbol} API返回空static_info列表")
            except Exception as e:
                logger.warning(f"⚠️ {symbol} 获取手数API调用失败: {type(e).__name__}: {e}")
        else:
            logger.debug(f"  ℹ️ SmartOrderRouter未配置quote_client，将尝试数据库")

        # 🔥 新增：尝试从数据库查询
        if lot_size is None and self.db:
            try:
                from sqlalchemy import select
                from longport_quant.persistence.models import SecurityStatic

                async with self.db.session() as session:
                    result = await session.execute(
                        select(SecurityStatic).where(
                            SecurityStatic.symbol == symbol
                        )
                    )
                    security = result.scalar_one_or_none()

                    if security and security.lot_size and security.lot_size > 0:
                        lot_size = security.lot_size
                        self._lot_size_cache[symbol] = lot_size
                        logger.info(f"  📊 {symbol} 手数(数据库): {lot_size}股/手")
                        return lot_size
                    else:
                        logger.debug(f"  ℹ️ {symbol} 数据库中无lot_size数据")
            except Exception as e:
                logger.debug(f"  ℹ️ {symbol} 数据库查询失败: {e}")

        # 使用默认值（但这可能导致订单失败！）
        default_lot_size = 1 if ".US" in symbol else 100
        self._lot_size_cache[symbol] = default_lot_size
        logger.warning(
            f"⚠️ {symbol} 使用默认手数: {default_lot_size}股/手 "
            f"(这可能与实际手数不符，可能导致订单失败！建议同步静态数据)"
        )
        return default_lot_size

    async def _validate_and_adjust_quantity(self, symbol: str, quantity: int) -> int:
        """
        验证并调整订单数量，确保是手数的整数倍

        Args:
            symbol: 股票代码
            quantity: 原始订单数量

        Returns:
            调整后的订单数量（手数的整数倍）
        """
        lot_size = await self._get_lot_size(symbol)

        # 检查是否为手数的整数倍
        if quantity % lot_size != 0:
            # 向下取整到最接近的手数倍数
            adjusted_qty = (quantity // lot_size) * lot_size
            logger.warning(
                f"  ⚠️ {symbol}: 订单数量{quantity}股不是手数{lot_size}的倍数，"
                f"已自动调整为{adjusted_qty}股（{adjusted_qty // lot_size}手）"
            )
            return adjusted_qty

        logger.debug(f"  ✅ {symbol}: 订单数量{quantity}股有效（{quantity // lot_size}手 × {lot_size}股/手）")
        return quantity

    async def _fallback_cash_estimate(
        self,
        symbol: str,
        price: float,
        lot_size: int
    ) -> int:
        """
        Fallback现金估算：当broker estimate返回0时的备用方案
        使用50%现金进行保守估算，保留50%安全边际

        Args:
            symbol: 股票代码
            price: 估算价格
            lot_size: 手数大小

        Returns:
            估算的可买数量（股）
        """
        try:
            # 获取币种现金
            currency = "HKD" if symbol.endswith(".HK") else "USD"

            # 使用trade_context获取账户余额（指定币种）
            balance_resp = await asyncio.to_thread(
                self.trade_context.account_balance,
                currency  # 只获取指定币种的余额
            )

            if not balance_resp or len(balance_resp) == 0:
                logger.debug(f"  ⚠️ {currency}账户余额为空")
                return 0

            balance = balance_resp[0]

            # 解析现金余额和买入力
            cash_available = 0.0
            buy_power = float(getattr(balance, 'buy_power', 0) or 0)

            # 从cash_infos中提取可用现金
            if hasattr(balance, 'cash_infos') and balance.cash_infos:
                for cash_info in balance.cash_infos:
                    if cash_info.currency == currency:
                        cash_available = float(getattr(cash_info, 'available_cash', 0) or 0)
                        break

            # 获取所有币种的余额信息（用于诊断）
            all_balances_resp = await asyncio.to_thread(
                self.trade_context.account_balance
            )

            all_balances = {}
            for bal in all_balances_resp:
                if hasattr(bal, 'cash_infos') and bal.cash_infos:
                    for cash_info in bal.cash_infos:
                        ccy = cash_info.currency
                        cash_val = float(getattr(cash_info, 'available_cash', 0) or 0)
                        power_val = float(getattr(bal, 'buy_power', 0) or 0)
                        all_balances[ccy] = {"cash": cash_val, "buy_power": power_val}

            # 🔍 跨币种债务诊断：检测"有现金但买入力为负"的情况
            if cash_available > 0 and buy_power < 0:
                logger.warning(
                    f"🔍 跨币种债务诊断 - {currency}:\n"
                    f"   {currency}现金: ${cash_available:,.2f} ✅\n"
                    f"   {currency}买入力: ${buy_power:,.2f} ❌\n"
                    f"   \n"
                    f"   📊 全账户状态:\n"
                    + "\n".join([
                        f"   • {ccy}: 现金=${bal['cash']:,.0f}, "
                        f"买入力=${bal['buy_power']:,.0f}"
                        for ccy, bal in sorted(all_balances.items())
                    ]) +
                    f"\n\n"
                    f"   ⚠️ 可能原因:\n"
                    f"   • 其他币种融资债务影响整体账户购买力\n"
                    f"   • LongPort风控将跨币种债务纳入购买力计算\n"
                    f"   \n"
                    f"   💡 建议:\n"
                    f"   • 系统将使用50%现金进行保守估算\n"
                    f"   • 考虑减仓释放购买力\n"
                    f"   • 或归还融资债务恢复购买力"
                )

            # 🔧 融资账户Fallback修复
            if cash_available <= 0:
                logger.info(f"  ⚠️ {currency}现金不足: ${cash_available:,.0f} (可能为融资账户)")

                # 尝试获取融资额度信息
                try:
                    margin_call = float(getattr(balance, 'margin_call', 0) or 0)
                    financing_amount = float(getattr(balance, 'financing_amount', 0) or 0)
                    remaining_finance = margin_call - abs(financing_amount)

                    logger.info(
                        f"  🔍 融资信息检测:\n"
                        f"     融资额度(margin_call): ${margin_call:,.2f}\n"
                        f"     已用融资(financing_amount): ${abs(financing_amount):,.2f}\n"
                        f"     剩余融资额度: ${remaining_finance:,.2f}"
                    )

                    # 检查剩余融资额度是否足够
                    min_purchase = price * lot_size * 2  # 至少能买2手
                    if remaining_finance > min_purchase:
                        # 使用30%融资额度进行保守估算（比现金更保守）
                        conservative_finance = remaining_finance * 0.3
                        estimated_qty = int(conservative_finance / price)
                        lots = int(estimated_qty // lot_size)

                        if lots > 0:
                            final_qty = lots * lot_size
                            logger.warning(
                                f"⚠️ Fallback融资估算 - {symbol}:\n"
                                f"   {currency}现金: ${cash_available:,.0f} ❌\n"
                                f"   剩余融资额度: ${remaining_finance:,.0f} ✅\n"
                                f"   保守策略: 使用30%融资 = ${conservative_finance:,.0f}\n"
                                f"   估算数量: {final_qty}股 ({lots}手 × {lot_size}股/手)\n"
                                f"   说明: 现金不足但融资额度充足，尝试融资估算"
                            )
                            return final_qty
                except Exception as e:
                    logger.debug(f"  融资额度检测失败: {e}")

                # 融资额度也不足，返回0
                logger.debug(f"  ❌ {currency}现金和融资额度均不足")
                return 0

            # 使用50%现金进行保守估算（现金账户）
            conservative_cash = cash_available * 0.5
            estimated_qty = int(conservative_cash / price)

            # 按手数取整
            lots = int(estimated_qty // lot_size)
            if lots <= 0:
                return 0

            final_qty = lots * lot_size

            logger.warning(
                f"⚠️ Fallback现金估算 - {symbol}:\n"
                f"   {currency}现金: ${cash_available:,.0f} ✅\n"
                f"   保守策略: 使用50%现金 = ${conservative_cash:,.0f}\n"
                f"   估算数量: {final_qty}股 ({lots}手 × {lot_size}股/手)\n"
                f"   说明: Broker estimate返回0，但现金充足，尝试保守估算"
            )

            return final_qty

        except Exception as e:
            logger.error(f"  ❌ Fallback现金估算失败: {e}")
            return 0

    async def execute_order(self, request: OrderRequest) -> ExecutionResult:
        """
        Execute an order using smart routing.

        Args:
            request: Order request details

        Returns:
            Execution result
        """
        try:
            logger.info(f"Executing order: {request.symbol} {request.side} {request.quantity}")

            # 🌙 盘后时段安全控制（仅美股）
            if request.symbol.endswith('.US'):
                from longport_quant.utils.market_hours import MarketHours
                us_session = MarketHours.get_us_session()

                if us_session == "AFTERHOURS":
                    # 从配置获取盘后限制
                    afterhours_force_limit = getattr(self._settings, 'afterhours_force_limit_orders', True)
                    afterhours_max_urgency = getattr(self._settings, 'afterhours_max_urgency', 3)

                    # 强制限价单
                    if afterhours_force_limit and request.order_type == "MARKET":
                        logger.warning(
                            f"[盘后安全] {request.symbol}: 盘后时段禁止市价单，"
                            f"自动转为限价单（MARKET → LIMIT）"
                        )
                        request.order_type = "LIMIT"
                        # 如果没有限价，使用当前价格
                        if request.limit_price is None:
                            market_data = await self._get_market_data(request.symbol)
                            if market_data:
                                request.limit_price = market_data.last_price

                    # 限制紧急度
                    if request.urgency > afterhours_max_urgency:
                        original_urgency = request.urgency
                        request.urgency = afterhours_max_urgency
                        logger.warning(
                            f"[盘后安全] {request.symbol}: 盘后紧急度过高，"
                            f"降低紧急度 {original_urgency} → {afterhours_max_urgency}"
                        )

                    # 强制PASSIVE策略（避免AGGRESSIVE）
                    if request.strategy == ExecutionStrategy.AGGRESSIVE:
                        request.strategy = ExecutionStrategy.PASSIVE
                        logger.warning(
                            f"[盘后安全] {request.symbol}: 盘后时段禁止AGGRESSIVE策略，"
                            f"改为PASSIVE策略"
                        )

                    logger.info(
                        f"🌙 [盘后订单] {request.symbol} {request.side} {request.quantity}\n"
                        f"   类型: {request.order_type} | 紧急度: {request.urgency} | 策略: {request.strategy.value}"
                    )

            # Update market data
            await self._update_market_data(request.symbol)

            # Validate order
            if not await self._validate_order(request):
                return ExecutionResult(
                    success=False,
                    error_message="Order validation failed"
                )

            # Determine execution strategy
            if request.strategy == ExecutionStrategy.ADAPTIVE:
                request.strategy = await self._select_strategy(request)

            # Route order based on strategy
            if request.strategy == ExecutionStrategy.AGGRESSIVE:
                result = await self._execute_aggressive(request)
            elif request.strategy == ExecutionStrategy.PASSIVE:
                result = await self._execute_passive(request)
            elif request.strategy == ExecutionStrategy.ICEBERG:
                result = await self._execute_iceberg(request)
            elif request.strategy == ExecutionStrategy.TWAP:
                result = await self._execute_twap(request)
            elif request.strategy == ExecutionStrategy.VWAP:
                result = await self._execute_vwap(request)
            else:
                result = await self._execute_standard(request)

            # Store execution result
            await self._store_execution_result(request, result)

            return result

        except Exception as e:
            logger.error(f"Order execution failed: {e}")
            return ExecutionResult(
                success=False,
                error_message=str(e)
            )

    async def _validate_order(self, request: OrderRequest) -> bool:
        """Validate order before execution."""
        # Check basic parameters
        if request.quantity <= 0:
            logger.error("Invalid quantity")
            return False

        if request.side not in ["BUY", "SELL"]:
            logger.error("Invalid order side")
            return False

        # Check limit price for limit orders
        if request.order_type == "LIMIT" and not request.limit_price:
            logger.error("Limit price required for limit orders")
            return False

        # Validate against current market price
        market_data = self._market_data_cache.get(request.symbol)
        if market_data:
            current_price = market_data.get('last_price', 0)

            if request.limit_price:
                # Check if limit price is reasonable
                price_diff = abs(request.limit_price - current_price) / current_price
                if price_diff > 0.1:  # More than 10% away from market
                    logger.warning(f"Limit price {request.limit_price} is far from market {current_price}")

        return True

    async def _select_strategy(self, request: OrderRequest) -> ExecutionStrategy:
        """Select appropriate execution strategy based on market conditions and safety controls."""
        from longport_quant.utils.market_hours import MarketHours

        market_data = self._market_data_cache.get(request.symbol, {})

        # 🔒 安全控制1：全局强制限价单开关
        if self._settings and self._settings.force_limit_orders:
            logger.debug("🔒 FORCE_LIMIT_ORDERS=True, 强制使用PASSIVE策略（限价单）")
            return ExecutionStrategy.PASSIVE

        # 🔒 安全控制2：应用最大紧急度上限
        max_urgency = 10  # 默认无上限
        if self._settings and hasattr(self._settings, 'max_urgency_level'):
            max_urgency = self._settings.max_urgency_level
            if request.urgency > max_urgency:
                logger.warning(
                    f"🔒 紧急度{request.urgency}超过上限{max_urgency}，已自动调整"
                )
                request.urgency = max_urgency

        # 🔒 安全控制3：盘外时段禁用市价单
        if self._settings and not self._settings.allow_market_orders_during_market_hours:
            current_market = MarketHours.get_current_market()
            if current_market == "NONE":
                logger.debug("🔒 市场休市且禁用盘外市价单，强制使用PASSIVE策略")
                return ExecutionStrategy.PASSIVE

        # High urgency - use aggressive (如果没有被上面的安全控制拦截)
        if request.urgency >= 8:
            return ExecutionStrategy.AGGRESSIVE

        # Large order - use iceberg or TWAP
        avg_volume = market_data.get('avg_volume', 0)
        if avg_volume > 0:
            order_size_pct = request.quantity / avg_volume
            if order_size_pct > 0.05:  # More than 5% of average volume
                if order_size_pct > 0.1:
                    return ExecutionStrategy.ICEBERG
                else:
                    return ExecutionStrategy.TWAP

        # Low urgency - use passive
        if request.urgency <= 3:
            return ExecutionStrategy.PASSIVE

        # Default to passive execution (limit orders for better control)
        return ExecutionStrategy.PASSIVE

    def _calculate_dynamic_limit_price(
        self,
        symbol: str,
        side: str,
        reference_price: float,
        current_market_price: float,
        max_slippage: float,
        market_data: dict
    ) -> tuple[float, bool]:
        """
        动态计算限价，控制滑点

        Args:
            symbol: 股票代码
            side: 订单方向 (BUY/SELL)
            reference_price: 初始参考价格
            current_market_price: 当前市场价格
            max_slippage: 最大允许滑点 (如0.01=1%)
            market_data: 实时行情数据 (包含bid/ask)

        Returns:
            (建议限价, 是否超过滑点限制)
        """
        # 计算当前市场价相对参考价的偏差
        price_deviation = abs(current_market_price - reference_price) / reference_price
        exceeds_slippage = price_deviation > max_slippage

        if side == "BUY":
            # 买入：限价不能超过参考价 * (1 + max_slippage)
            max_acceptable_price = reference_price * (1 + max_slippage)

            # 获取当前卖一价
            ask_price = market_data.get('ask', current_market_price)
            if ask_price == 0:
                ask_price = current_market_price

            # 在ask价基础上略微提高（提高成交概率）
            # 转换为 float 避免 Decimal * float 类型错误
            suggested_price = float(ask_price) * 1.001

            # 取较小值，确保不超过滑点上限
            limit_price = min(suggested_price, max_acceptable_price)

            # 🔥 舍入到有效的tick size
            limit_price = self._round_price_to_tick(symbol, limit_price)

            logger.debug(
                f"动态限价计算(BUY): 参考=${reference_price:.2f}, "
                f"市场=${current_market_price:.2f}, Ask=${ask_price:.2f}, "
                f"建议限价=${limit_price:.2f}, 偏差={price_deviation*100:.2f}%, "
                f"超限={exceeds_slippage}"
            )

        else:  # SELL
            # 卖出：限价不能低于参考价 * (1 - max_slippage)
            min_acceptable_price = reference_price * (1 - max_slippage)

            # 获取当前买一价
            bid_price = market_data.get('bid', current_market_price)
            if bid_price == 0:
                bid_price = current_market_price

            # 在bid价基础上略微降低（提高成交概率）
            # 转换为 float 避免 Decimal * float 类型错误
            suggested_price = float(bid_price) * 0.999

            # 取较大值，确保不低于滑点下限
            limit_price = max(suggested_price, min_acceptable_price)

            # 🔥 舍入到有效的tick size
            limit_price = self._round_price_to_tick(symbol, limit_price)

            logger.debug(
                f"动态限价计算(SELL): 参考=${reference_price:.2f}, "
                f"市场=${current_market_price:.2f}, Bid=${bid_price:.2f}, "
                f"建议限价=${limit_price:.2f}, 偏差={price_deviation*100:.2f}%, "
                f"超限={exceeds_slippage}"
            )

        return limit_price, exceeds_slippage

    async def _execute_aggressive(self, request: OrderRequest) -> ExecutionResult:
        """Execute order aggressively using market orders."""
        logger.info(f"Executing aggressive order for {request.symbol}")

        try:
            # 🔥 验证并调整订单数量（确保是手数的整数倍）
            original_quantity = request.quantity
            request.quantity = await self._validate_and_adjust_quantity(request.symbol, request.quantity)

            if request.quantity != original_quantity:
                logger.info(f"  📊 数量已调整: {original_quantity} → {request.quantity}股")

            if request.quantity == 0:
                logger.error(f"  ❌ 调整后数量为0，无法下单")
                return ExecutionResult(success=False, error_message="调整后数量为0，无法下单")

            # Submit market order
            order_side = OrderSide.Buy if request.side == "BUY" else OrderSide.Sell

            # Wrap synchronous SDK call with asyncio.to_thread
            # 正确的参数顺序: symbol, order_type, side, quantity, time_in_force, price, ...
            resp = await asyncio.to_thread(
                self.trade_context.submit_order,
                request.symbol,
                OrderType.MO,  # order_type: Market Order
                order_side,     # side
                request.quantity,
                TimeInForceType.Day,
                None,  # price (not needed for market orders)
                None,  # trigger_price
                None,  # limit_offset
                None,  # trailing_amount
                None,  # trailing_percent
                None   # expire_date
            )

            # Track order
            self._active_orders[resp.order_id] = request

            # Wait for fill
            filled_qty, avg_price = await self._wait_for_fill(resp.order_id, timeout=10)

            # Calculate slippage
            market_data = self._market_data_cache.get(request.symbol, {})
            reference_price = market_data.get('last_price', avg_price)
            slippage = abs(avg_price - reference_price) / reference_price if reference_price > 0 else 0

            return ExecutionResult(
                success=True,
                order_id=resp.order_id,
                filled_quantity=filled_qty,
                average_price=avg_price,
                slippage=slippage,
                execution_time=datetime.now()
            )

        except Exception as e:
            logger.error(f"Aggressive execution failed: {e}")
            return ExecutionResult(success=False, error_message=str(e))

    async def _execute_passive(self, request: OrderRequest) -> ExecutionResult:
        """Execute order passively using limit orders."""
        logger.info(f"Executing passive order for {request.symbol}")

        try:
            market_data = self._market_data_cache.get(request.symbol, {})

            # Determine limit price
            if request.limit_price:
                limit_price = request.limit_price
            else:
                # Place at favorable price
                bid = market_data.get('bid', 0)
                ask = market_data.get('ask', 0)

                if request.side == "BUY":
                    limit_price = bid  # Join the bid
                else:
                    limit_price = ask  # Join the ask

            # 🔥 确保价格符合tick size（统一处理）
            original_limit_price = limit_price
            limit_price = self._round_price_to_tick(request.symbol, limit_price)

            logger.info(f"  💰 下单参数: {request.side} {request.quantity}股 @ ${limit_price:.2f}")

            # 打印详细参数用于调试
            logger.debug(
                f"  📋 订单详细参数:\n"
                f"     symbol={request.symbol}\n"
                f"     side={request.side}\n"
                f"     quantity={request.quantity}\n"
                f"     limit_price(原始)=${original_limit_price:.4f}\n"
                f"     limit_price(调整后)=${limit_price:.4f}\n"
                f"     order_type=LO\n"
                f"     time_in_force=Day"
            )

            # 🔥 验证并调整订单数量（确保是手数的整数倍）
            original_quantity = request.quantity
            request.quantity = await self._validate_and_adjust_quantity(request.symbol, request.quantity)

            if request.quantity != original_quantity:
                logger.info(f"  📊 数量已调整: {original_quantity} → {request.quantity}股")

            if request.quantity == 0:
                logger.error(f"  ❌ 调整后数量为0，无法下单")
                return ExecutionResult(success=False, error_message="调整后数量为0，无法下单")

            # 🔍 下单前调用券商可买上限估算（仅BUY/限价单）
            if request.side == "BUY":
                try:
                    # 使用已校正的限价作为估算价格（float更兼容该接口）
                    est_price = float(limit_price)
                    resp = await asyncio.to_thread(
                        self.trade_context.estimate_max_purchase_quantity,
                        request.symbol,
                        OrderType.LO,
                        OrderSide.Buy,
                        est_price,
                        None,  # currency
                        None,  # order_id
                        False  # fractional_shares
                    )

                    cash_max = int(getattr(resp, "cash_max_qty", 0) or 0)
                    margin_max = int(getattr(resp, "margin_max_qty", 0) or 0)
                    allow_max = max(cash_max, margin_max)

                    logger.debug(
                        f"  🧮 券商可买上限估算: 现金={cash_max}, 融资={margin_max}, 取最大={allow_max}"
                    )

                    if allow_max <= 0:
                        logger.warning(
                            f"  ⚠️ {request.symbol}: 券商估算可买数量为0，尝试Fallback现金估算..."
                        )

                        # Fallback: 使用现金保守估算
                        lot_size = await self._get_lot_size(request.symbol)
                        fallback_quantity = await self._fallback_cash_estimate(
                            symbol=request.symbol,
                            price=limit_price,
                            lot_size=lot_size
                        )

                        if fallback_quantity <= 0:
                            logger.error("  ❌ Fallback估算也失败，跳过下单")
                            return ExecutionResult(success=False, error_message="可买数量为0（Fallback也失败）")

                        # 检查请求数量是否超过Fallback估算
                        if request.quantity > fallback_quantity:
                            logger.warning(
                                f"  ⚠️ 请求数量{request.quantity}超过Fallback估算{fallback_quantity}，"
                                f"调整为{fallback_quantity}"
                            )
                            request.quantity = fallback_quantity

                        logger.info(
                            f"  ✅ Fallback估算成功，继续下单: {request.quantity}股"
                        )
                        # 跳过后续的allow_max检查
                        allow_max = fallback_quantity

                    if request.quantity > allow_max:
                        logger.error(
                            f"  ❌ 请求数量{request.quantity}超过券商可买上限{allow_max}，跳过下单"
                        )
                        return ExecutionResult(
                            success=False,
                            error_message=f"买入数量超过可买上限({allow_max})"
                        )
                except Exception as e:
                    # 估算失败时不中断下单流程，仅警告
                    logger.warning(f"  ⚠️ 可买上限估算失败，继续下单: {e}")

            # 🔍 最终手数验证（提交前最后检查）
            final_lot_size = await self._get_lot_size(request.symbol)
            if request.quantity % final_lot_size != 0:
                original_qty = request.quantity
                request.quantity = (request.quantity // final_lot_size) * final_lot_size
                logger.warning(
                    f"  ⚠️ 提交前发现手数不匹配，自动调整: "
                    f"{original_qty}股 → {request.quantity}股 "
                    f"({request.quantity // final_lot_size}手 × {final_lot_size}股/手)"
                )

                if request.quantity <= 0:
                    logger.error(f"  ❌ 调整后数量为0，无法下单")
                    return ExecutionResult(success=False, error_message="调整后数量为0")

            # Submit limit order
            order_side = OrderSide.Buy if request.side == "BUY" else OrderSide.Sell

            # 转换为 Decimal 并打印最终值
            price_decimal = Decimal(str(limit_price))
            logger.debug(f"  🔢 最终提交价格(Decimal): {price_decimal}")
            logger.debug(f"  📦 最终提交数量: {request.quantity}股 ({request.quantity // final_lot_size}手)")

            # Wrap synchronous SDK call with asyncio.to_thread
            # 正确的参数顺序: symbol, order_type, side, quantity, time_in_force, price, ...
            # 注意：outside_rth参数已移除，因为不是所有SDK版本都支持
            resp = await asyncio.to_thread(
                self.trade_context.submit_order,
                request.symbol,
                OrderType.LO,  # order_type: Limit Order
                order_side,     # side
                request.quantity,
                TimeInForceType.Day,
                price_decimal,  # price
                None,  # trigger_price
                None,  # limit_offset
                None,  # trailing_amount
                None,  # trailing_percent
                None  # expire_date
            )

            logger.info(f"  ✅ 订单已提交: order_id={resp.order_id}")

            # Track order
            self._active_orders[resp.order_id] = request

            # Wait for fill with longer timeout
            filled_qty, avg_price = await self._wait_for_fill(resp.order_id, timeout=60)

            # If not fully filled, may need to adjust
            if filled_qty < request.quantity:
                logger.warning(f"Partial fill: {filled_qty}/{request.quantity}")

            # 🔥 判断订单是否成功：至少有部分成交
            success = filled_qty > 0
            if not success:
                logger.error(f"  ❌ 订单未成交（filled_qty=0），标记为失败")

            return ExecutionResult(
                success=success,
                order_id=resp.order_id,
                filled_quantity=filled_qty,
                average_price=avg_price,
                execution_time=datetime.now(),
                error_message="订单被拒绝或未成交" if not success else None
            )

        except Exception as e:
            error_str = str(e)

            # 🔥 增强错误处理1：手数倍数错误自动重试（602001）
            if "602001" in error_str or "lot size" in error_str.lower():
                logger.warning(f"  ⚠️ 遇到手数倍数错误(602001)，尝试自动调整数量并重试...")
                logger.debug(f"  原始错误: {error_str}")

                try:
                    # 重新获取正确的lot_size
                    correct_lot_size = await self._get_lot_size(request.symbol)
                    logger.info(f"  📏 正确的lot_size: {correct_lot_size}股/手")

                    # 调整数量到正确的手数倍数
                    adjusted_quantity = (request.quantity // correct_lot_size) * correct_lot_size

                    if adjusted_quantity <= 0:
                        # 至少买一手
                        adjusted_quantity = correct_lot_size
                        logger.warning(f"  ⚠️ 调整后数量为0，改为最小1手: {adjusted_quantity}股")

                    logger.info(
                        f"  🔄 数量调整: {request.quantity}股 → {adjusted_quantity}股 "
                        f"({adjusted_quantity // correct_lot_size}手 × {correct_lot_size}股/手)"
                    )

                    # 如果调整后数量与原数量相同，说明不是手数问题，直接失败
                    if adjusted_quantity == request.quantity:
                        logger.error(
                            f"  ❌ 数量已经是手数倍数({request.quantity} = {request.quantity // correct_lot_size}手 × {correct_lot_size}股)，"
                            f"但仍报手数错误，可能是其他原因"
                        )
                        return ExecutionResult(success=False, error_message=error_str)

                    # 重新提交订单（使用调整后的数量）
                    order_side = OrderSide.Buy if request.side == "BUY" else OrderSide.Sell
                    price_decimal = Decimal(str(limit_price))

                    logger.info(f"  💰 重试订单参数: {request.side} {adjusted_quantity}股 @ ${limit_price:.2f}")

                    resp = await asyncio.to_thread(
                        self.trade_context.submit_order,
                        request.symbol,
                        OrderType.LO,
                        order_side,
                        adjusted_quantity,  # 使用调整后的数量
                        TimeInForceType.Day,
                        price_decimal,
                        None, None, None, None, None
                    )

                    logger.success(f"  ✅ 手数调整后重试成功！订单已提交: order_id={resp.order_id}")

                    # Track order
                    self._active_orders[resp.order_id] = request

                    # Wait for fill
                    filled_qty, avg_price = await self._wait_for_fill(resp.order_id, timeout=60)

                    if filled_qty < adjusted_quantity:
                        logger.warning(f"Partial fill: {filled_qty}/{adjusted_quantity}")

                    success = filled_qty > 0
                    return ExecutionResult(
                        success=success,
                        order_id=resp.order_id,
                        filled_quantity=filled_qty,
                        average_price=avg_price,
                        execution_time=datetime.now(),
                        error_message="订单被拒绝或未成交" if not success else None
                    )

                except Exception as retry_error:
                    logger.error(f"  ❌ 手数调整重试也失败: {retry_error}")
                    return ExecutionResult(
                        success=False,
                        error_message=f"原始错误: {error_str}, 重试错误: {str(retry_error)}"
                    )

            # 🔥 增强错误处理2：对602035错误进行自动重试（使用市场价格）
            if "602035" in error_str or "Wrong bid size" in error_str:
                logger.warning(f"  ⚠️ 遇到602035错误，尝试使用实时市场价格重试...")
                logger.debug(f"  原始错误: {error_str}")

                try:
                    # 重新获取最新市场数据
                    await self._update_market_data(request.symbol)
                    market_data = self._market_data_cache.get(request.symbol, {})

                    # 使用市场价格（ask for buy, bid for sell）
                    if request.side == "BUY":
                        retry_price = market_data.get('ask', limit_price)
                        if retry_price <= 0:
                            retry_price = market_data.get('last_price', limit_price)
                        logger.info(f"  🔄 重试价格策略: 使用ASK价格 ${retry_price:.2f}")
                    else:
                        retry_price = market_data.get('bid', limit_price)
                        if retry_price <= 0:
                            retry_price = market_data.get('last_price', limit_price)
                        logger.info(f"  🔄 重试价格策略: 使用BID价格 ${retry_price:.2f}")

                    # 调整到tick size
                    retry_price = self._round_price_to_tick(request.symbol, retry_price)

                    # 转换为Decimal
                    price_decimal = Decimal(str(retry_price))
                    logger.info(f"  💰 重试订单参数: {request.side} {request.quantity}股 @ ${retry_price:.2f}")

                    # 重试提交订单
                    order_side = OrderSide.Buy if request.side == "BUY" else OrderSide.Sell
                    resp = await asyncio.to_thread(
                        self.trade_context.submit_order,
                        request.symbol,
                        OrderType.LO,
                        order_side,
                        request.quantity,
                        TimeInForceType.Day,
                        price_decimal,
                        None, None, None, None, None
                    )

                    logger.success(f"  ✅ 重试成功！订单已提交: order_id={resp.order_id}")

                    # Track order
                    self._active_orders[resp.order_id] = request

                    # Wait for fill
                    filled_qty, avg_price = await self._wait_for_fill(resp.order_id, timeout=60)

                    if filled_qty < request.quantity:
                        logger.warning(f"Partial fill: {filled_qty}/{request.quantity}")

                    return ExecutionResult(
                        success=True,
                        order_id=resp.order_id,
                        filled_quantity=filled_qty,
                        average_price=avg_price,
                        execution_time=datetime.now()
                    )

                except Exception as retry_error:
                    logger.error(f"  ❌ 重试也失败: {retry_error}")
                    return ExecutionResult(success=False, error_message=f"原始错误: {error_str}, 重试错误: {str(retry_error)}")

            logger.error(f"Passive execution failed: {e}")
            return ExecutionResult(success=False, error_message=str(e))

    async def _execute_iceberg(self, request: OrderRequest) -> ExecutionResult:
        """Execute large order as iceberg (hidden size)."""
        logger.info(f"Executing iceberg order for {request.symbol}")

        # Calculate slice size (show only 10% at a time)
        visible_size = max(100, request.quantity // 10)
        slices = self._create_order_slices(request, visible_size)

        total_filled = 0
        total_value = 0.0
        child_orders = []

        for slice_order in slices:
            # Execute each slice
            slice_request = OrderRequest(
                symbol=request.symbol,
                side=request.side,
                quantity=slice_order.quantity,
                order_type=request.order_type,
                limit_price=slice_order.limit_price or request.limit_price,
                strategy=ExecutionStrategy.AGGRESSIVE,
                urgency=request.urgency,
                signal=request.signal
            )

            result = await self._execute_aggressive(slice_request)

            if result.success:
                total_filled += result.filled_quantity
                total_value += result.filled_quantity * result.average_price
                child_orders.append(result.order_id)
                slice_order.executed = True
                slice_order.order_id = result.order_id
            else:
                logger.warning(f"Slice execution failed: {result.error_message}")
                break

            # Small delay between slices
            await asyncio.sleep(1)

        avg_price = total_value / total_filled if total_filled > 0 else 0

        return ExecutionResult(
            success=total_filled > 0,
            filled_quantity=total_filled,
            average_price=avg_price,
            execution_time=datetime.now(),
            child_orders=child_orders
        )

    def _calculate_twap_slices(self, symbol: str, quantity: int, desired_slices: int) -> tuple[bool, int, str]:
        """
        计算TWAP订单的最佳切片数，智能调整以保证每片都是整手

        Args:
            symbol: 股票代码
            quantity: 订单数量（股数）
            desired_slices: 期望的切片数量

        Returns:
            (是否适合TWAP, 实际切片数, 原因说明)
        """
        # 使用保守的手数规格估算（避免异步API调用）
        if ".US" in symbol:
            assumed_lot_size = 1
            min_lots_per_slice = 100  # 美股每个切片至少100股
            min_total_lots = 1000  # 总量至少1000股
        else:
            # 港股保守估计 - 只有大订单才使用TWAP，小订单直接LO
            assumed_lot_size = 1000  # 对于蓝筹股如1398.HK
            min_lots_per_slice = 3  # 每个切片至少3手（3000股）
            min_total_lots = 20  # 总量至少20手（20000股）才使用TWAP

        # 计算总手数
        total_lots = quantity // assumed_lot_size

        # 检查1: 订单是否为整手
        if quantity % assumed_lot_size != 0:
            return False, 0, f"订单{quantity}股不是整手（假设{assumed_lot_size}股/手）"

        # 检查2: 总量是否足够
        if total_lots < min_total_lots:
            return False, 0, f"总共{total_lots}手（{quantity}股），低于TWAP最低要求{min_total_lots}手"

        # 检查3: 找到合适的切片数（能整除总手数，且每片>=最小手数）
        # 优先选择接近desired_slices的值
        candidates = []

        # 向下搜索（从desired_slices到1）
        for slices in range(desired_slices, 0, -1):
            if total_lots % slices == 0:
                lots_per_slice = total_lots // slices
                if lots_per_slice >= min_lots_per_slice:
                    candidates.append((slices, lots_per_slice))

        # 向上搜索（从desired_slices+1开始，但不超过总手数）
        for slices in range(desired_slices + 1, min(total_lots + 1, desired_slices + 5)):
            if total_lots % slices == 0:
                lots_per_slice = total_lots // slices
                if lots_per_slice >= min_lots_per_slice:
                    candidates.append((slices, lots_per_slice))

        if not candidates:
            return False, 0, f"无法找到合适切片数（{total_lots}手，每片需≥{min_lots_per_slice}手）"

        # 选择最接近desired_slices的方案
        best = min(candidates, key=lambda x: abs(x[0] - desired_slices))
        actual_slices, lots_per_slice = best

        reason = (
            f"TWAP切片: {actual_slices}片 × {lots_per_slice}手/片 "
            f"({lots_per_slice * assumed_lot_size}股/片) = {total_lots}手（{quantity}股）"
        )
        if actual_slices != desired_slices:
            reason += f" [已从{desired_slices}片调整]"

        return True, actual_slices, reason

    async def _execute_twap(self, request: OrderRequest) -> ExecutionResult:
        """Execute order using Time-Weighted Average Price strategy with dynamic slippage control."""
        logger.info(f"Executing TWAP order for {request.symbol}")

        # Divide order into time slices (e.g., execute over 30 minutes)
        duration_minutes = 30
        desired_slices = min(10, max(3, request.quantity // 1000))

        # 🔥 智能计算TWAP切片数（自动调整以保证整手）
        should_use, num_slices, reason = self._calculate_twap_slices(
            request.symbol, request.quantity, desired_slices
        )
        if not should_use:
            logger.warning(f"TWAP不适合此订单，降级为单个LO限价单: {reason}")
            logger.info(f"  原订单: {request.quantity}股，期望{desired_slices}个切片")
            logger.info(f"  降级策略: 使用单个限价单执行")
            # 降级为单个限价单
            return await self._execute_passive(request)

        # 使用调整后的切片数
        logger.info(f"✅ {reason}")
        slice_size = request.quantity // num_slices
        interval_seconds = (duration_minutes * 60) / num_slices

        # 保存初始参考价格（用于滑点计算）
        reference_price = request.limit_price if request.limit_price else 0.0
        max_slippage = request.max_slippage if request.max_slippage else 0.02  # 默认2%
        use_dynamic_pricing = request.max_slippage is not None and request.max_slippage > 0

        total_filled = 0
        total_value = 0.0
        child_orders = []
        cumulative_slippage = 0.0  # 累计滑点

        logger.info(
            f"TWAP配置: 切片数={num_slices}, 间隔={interval_seconds:.0f}秒, "
            f"参考价=${reference_price:.2f}, 最大滑点={max_slippage*100:.1f}%, "
            f"动态定价={'启用' if use_dynamic_pricing else '禁用'}"
        )

        for i in range(num_slices):
            # Calculate slice quantity
            if i == num_slices - 1:
                # Last slice gets remainder
                slice_qty = request.quantity - total_filled
            else:
                slice_qty = slice_size

            # 🔥 更新市场数据（获取最新行情）
            await self._update_market_data(request.symbol)
            market_data = self._market_data_cache.get(request.symbol, {})
            current_market_price = market_data.get('last_price', reference_price)

            # 🔥 计算动态限价（如果启用）
            if use_dynamic_pricing and reference_price > 0:
                slice_limit_price, exceeds_slippage = self._calculate_dynamic_limit_price(
                    symbol=request.symbol,
                    side=request.side,
                    reference_price=reference_price,
                    current_market_price=current_market_price,
                    max_slippage=max_slippage,
                    market_data=market_data
                )

                # 🔥 检查是否超过滑点限制
                if exceeds_slippage:
                    logger.warning(
                        f"⚠️ TWAP切片{i+1}/{num_slices}: 市场价格偏离过大 "
                        f"(参考=${reference_price:.2f}, 当前=${current_market_price:.2f}, "
                        f"偏差={(abs(current_market_price - reference_price) / reference_price)*100:.2f}% > {max_slippage*100:.1f}%)"
                    )

                    # 检查累计滑点是否已经很高
                    if cumulative_slippage > max_slippage * 1.2:
                        logger.error(
                            f"❌ TWAP停止执行: 累计滑点{cumulative_slippage*100:.2f}% "
                            f"超过限制{max_slippage*1.2*100:.2f}%"
                        )
                        break  # 停止执行剩余切片
            else:
                # 使用固定限价（原有逻辑）
                slice_limit_price = request.limit_price

            logger.info(
                f"  📊 TWAP切片{i+1}/{num_slices}: "
                f"{slice_qty}股 @ ${slice_limit_price:.2f} "
                f"(市场=${current_market_price:.2f})"
            )

            # Execute slice
            slice_request = OrderRequest(
                symbol=request.symbol,
                side=request.side,
                quantity=slice_qty,
                order_type="LIMIT",
                limit_price=slice_limit_price,  # 🔥 使用动态限价
                strategy=ExecutionStrategy.PASSIVE,
                urgency=request.urgency,
                signal=request.signal
            )

            result = await self._execute_passive(slice_request)

            if result.success:
                total_filled += result.filled_quantity
                total_value += result.filled_quantity * result.average_price
                child_orders.append(result.order_id)

                # 🔥 计算本切片的滑点
                if reference_price > 0 and result.average_price > 0:
                    slice_slippage = abs(result.average_price - reference_price) / reference_price
                    weight = result.filled_quantity / request.quantity
                    cumulative_slippage += slice_slippage * weight

                    logger.info(
                        f"  ✅ 切片{i+1}成交: 数量={result.filled_quantity}股, "
                        f"均价=${result.average_price:.2f}, "
                        f"滑点={slice_slippage*100:.2f}%, "
                        f"累计滑点={cumulative_slippage*100:.2f}%"
                    )
            else:
                logger.warning(f"  ⚠️ TWAP切片{i+1}/{num_slices}执行失败")

            # Wait before next slice
            if i < num_slices - 1:
                await asyncio.sleep(interval_seconds)

        avg_price = total_value / total_filled if total_filled > 0 else 0

        # 最终日志输出
        logger.info(
            f"📊 TWAP执行完成: 成交{total_filled}/{request.quantity}股, "
            f"均价=${avg_price:.2f}, 累计滑点={cumulative_slippage*100:.2f}%, "
            f"子订单数={len(child_orders)}"
        )

        return ExecutionResult(
            success=total_filled > 0,
            filled_quantity=total_filled,
            average_price=avg_price,
            slippage=cumulative_slippage,  # 🔥 返回累计滑点
            execution_time=datetime.now(),
            child_orders=child_orders
        )

    async def _execute_vwap(self, request: OrderRequest) -> ExecutionResult:
        """Execute order using Volume-Weighted Average Price strategy."""
        logger.info(f"Executing VWAP order for {request.symbol}")

        # Get historical volume pattern
        volume_profile = await self._get_volume_profile(request.symbol)

        if not volume_profile:
            # Fallback to TWAP if no volume profile
            return await self._execute_twap(request)

        total_filled = 0
        total_value = 0.0
        child_orders = []

        # Execute based on volume profile
        for time_slot, volume_pct in volume_profile.items():
            # Calculate slice size based on volume percentage
            slice_qty = int(request.quantity * volume_pct)

            if slice_qty <= 0:
                continue

            # Execute slice
            slice_request = OrderRequest(
                symbol=request.symbol,
                side=request.side,
                quantity=slice_qty,
                order_type=request.order_type,
                limit_price=request.limit_price,
                strategy=ExecutionStrategy.ADAPTIVE,
                urgency=request.urgency,
                signal=request.signal
            )

            result = await self._execute_standard(slice_request)

            if result.success:
                total_filled += result.filled_quantity
                total_value += result.filled_quantity * result.average_price
                child_orders.append(result.order_id)

            # Wait for next time slot
            await asyncio.sleep(60)  # 1 minute intervals

        avg_price = total_value / total_filled if total_filled > 0 else 0

        return ExecutionResult(
            success=total_filled > 0,
            filled_quantity=total_filled,
            average_price=avg_price,
            execution_time=datetime.now(),
            child_orders=child_orders
        )

    async def _execute_standard(self, request: OrderRequest) -> ExecutionResult:
        """Execute standard order."""
        market_data = self._market_data_cache.get(request.symbol, {})
        spread = market_data.get('ask', 0) - market_data.get('bid', 0)

        # Use limit order if spread is wide
        if spread > 0 and market_data.get('last_price', 0) > 0:
            spread_pct = spread / market_data['last_price']
            if spread_pct > 0.002:  # Spread > 0.2%
                return await self._execute_passive(request)

        # Otherwise use market order
        return await self._execute_aggressive(request)

    async def _update_market_data(self, symbol: str) -> None:
        """Update cached market data with timeout to prevent blocking."""
        try:
            # Add timeout to prevent blocking if database is unavailable
            async with asyncio.timeout(2.0):  # 2 second timeout
                # Get real-time quote
                async with self.db.session() as session:
                    stmt = select(RealtimeQuote).where(
                        RealtimeQuote.symbol == symbol
                    ).order_by(RealtimeQuote.timestamp.desc()).limit(1)

                    result = await session.execute(stmt)
                    quote = result.scalar_one_or_none()

                    if quote:
                        self._market_data_cache[symbol] = {
                            'last_price': float(quote.last_done) if quote.last_done else 0,
                            'bid': float(quote.bid_price) if quote.bid_price else 0,
                            'ask': float(quote.ask_price) if quote.ask_price else 0,
                            'bid_volume': quote.bid_volume,
                            'ask_volume': quote.ask_volume,
                            'volume': quote.volume,
                            'timestamp': quote.timestamp
                        }

                        # Estimate average volume (simplified)
                        self._market_data_cache[symbol]['avg_volume'] = quote.volume / 4  # Assume 4 hours into trading

        except asyncio.TimeoutError:
            logger.warning(f"Database query timeout for {symbol}, skipping market data update")
        except Exception as e:
            logger.error(f"Failed to update market data: {e}")

    async def _wait_for_fill(
        self,
        order_id: str,
        timeout: int = 30
    ) -> Tuple[int, float]:
        """Wait for order to be filled."""
        start_time = datetime.now()
        filled_quantity = 0
        total_value = 0.0
        poll_count = 0

        logger.info(f"  ⏳ 开始监控订单成交: {order_id}, 超时={timeout}秒")

        while (datetime.now() - start_time).seconds < timeout:
            try:
                poll_count += 1

                # 🔥 直接通过 order_id 查询订单（精确查询，避免多账号混淆）
                # 使用 order_detail 而不是 today_orders，确保查询的是当前账号的订单
                order = await asyncio.to_thread(self.trade_context.order_detail, order_id)

                # 🔥 记录订单状态（每5秒记录一次）
                # Use correct attribute names: executed_quantity, executed_price
                if poll_count % 5 == 1 or poll_count == 1:
                    logger.debug(
                        f"  📊 订单状态检查 (#{poll_count}): "
                        f"status={order.status}, "
                        f"executed={order.executed_quantity}/{order.quantity}, "
                        f"price=${order.price}"
                    )

                # Convert status to string for comparison
                status_str = str(order.status)

                if "Filled" in status_str and "Partially" not in status_str:
                    # Fully filled
                    # 转换为 int 避免 Decimal 类型错误
                    filled_quantity = int(order.executed_quantity)
                    if order.executed_quantity > 0:
                        # Use executed_price directly from order
                        avg_price = float(order.executed_price)
                        logger.info(f"  ✅ 订单已完全成交: {filled_quantity}股 @ ${avg_price:.2f}")
                        return filled_quantity, avg_price

                elif "PartiallyFilled" in status_str or "Partially" in status_str:
                    # Partially filled - continue waiting
                    # 转换为 int 避免 Decimal 类型错误
                    filled_quantity = int(order.executed_quantity)
                    if poll_count % 5 == 0:
                        logger.info(f"  ⏳ 订单部分成交: {filled_quantity}股，继续等待...")

                elif any(x in status_str for x in ["Rejected", "Cancelled", "Expired"]):
                    logger.warning(f"  ❌ 订单异常状态: {status_str}")
                    # Log the rejection reason if available
                    if hasattr(order, 'msg') and order.msg:
                        logger.warning(f"  ❌ 拒绝原因: {order.msg}")
                    # 打印更多订单详细信息以便调试
                    logger.warning(f"  📋 订单详情: symbol={order.symbol}, side={order.side}, "
                                 f"quantity={order.quantity}, price={order.price}, "
                                 f"type={order.order_type}")
                    # 尝试打印所有可用属性
                    try:
                        order_attrs = {k: v for k, v in vars(order).items()
                                     if not k.startswith('_') and v is not None}
                        logger.debug(f"  📊 订单所有属性: {order_attrs}")
                    except Exception as e:
                        logger.debug(f"  ⚠️ 无法获取订单属性: {e}")
                    return 0, 0.0

                elif "NewStatus" in status_str or "Pending" in status_str:
                    # Order is pending, continue waiting
                    if poll_count % 10 == 1:
                        logger.debug(f"  ⏳ 订单等待成交中: {status_str}")

            except Exception as e:
                # 可能是订单刚提交，还未同步到系统，或者是其他错误
                if poll_count <= 3:
                    logger.warning(f"  ⚠️ 查询订单{order_id}失败 (尝试{poll_count}/3): {e}")
                else:
                    logger.error(f"  ❌ 检查订单状态时出错: {e}")

            await asyncio.sleep(1)

        logger.warning(f"  ⏰ 订单等待超时({timeout}秒): {order_id}, 已轮询{poll_count}次")

        # 返回部分成交数量（如果有）
        if filled_quantity > 0:
            avg_price = total_value / filled_quantity
            logger.info(f"  ⚠️ 超时但有部分成交: {filled_quantity}股 @ ${avg_price:.2f}")
            return filled_quantity, avg_price

        return 0, 0.0

    def _create_order_slices(
        self,
        request: OrderRequest,
        slice_size: int
    ) -> List[OrderSlice]:
        """Create order slices for large orders."""
        slices = []
        remaining = request.quantity
        slice_num = 0

        while remaining > 0:
            qty = min(slice_size, remaining)

            slice_order = OrderSlice(
                parent_id=f"{request.symbol}_{datetime.now().timestamp()}",
                slice_number=slice_num,
                quantity=qty,
                order_type=request.order_type,
                limit_price=request.limit_price
            )

            slices.append(slice_order)
            remaining -= qty
            slice_num += 1

        return slices

    async def _get_volume_profile(self, symbol: str) -> Dict[str, float]:
        """Get historical volume profile for VWAP execution."""
        # Simplified volume profile (in production, would analyze historical data)
        # Returns percentage of daily volume typically traded in each time period
        return {
            "09:30-10:00": 0.15,
            "10:00-10:30": 0.12,
            "10:30-11:00": 0.10,
            "11:00-11:30": 0.08,
            "11:30-12:00": 0.07,
            "12:00-13:00": 0.08,
            "13:00-13:30": 0.08,
            "13:30-14:00": 0.10,
            "14:00-14:30": 0.10,
            "14:30-15:00": 0.12
        }

    async def _store_execution_result(
        self,
        request: OrderRequest,
        result: ExecutionResult
    ) -> None:
        """Store execution result in database."""
        if not result.success:
            return

        try:
            async with self.db.session() as session:
                # Store main order
                order_data = {
                    'order_id': result.order_id,
                    'symbol': request.symbol,
                    'side': request.side,
                    'quantity': request.quantity,
                    'price': result.average_price,
                    'status': 'FILLED' if result.filled_quantity == request.quantity else 'PARTIAL',
                    'created_at': result.execution_time or datetime.now(),
                    'updated_at': datetime.now()
                }

                stmt = insert(OrderRecord).values(order_data)
                stmt = stmt.on_conflict_do_update(
                    index_elements=['order_id'],
                    set_={'status': stmt.excluded.status, 'updated_at': stmt.excluded.updated_at}
                )
                await session.execute(stmt)

                # Store fills
                if result.filled_quantity > 0:
                    fill_data = {
                        'order_id': result.order_id,
                        'trade_id': f"{result.order_id}_001",
                        'symbol': request.symbol,
                        'quantity': result.filled_quantity,
                        'price': result.average_price,
                        'filled_at': result.execution_time or datetime.now()
                    }

                    fill_stmt = insert(FillRecord).values(fill_data)
                    await session.execute(fill_stmt)

                await session.commit()

        except Exception as e:
            logger.error(f"Failed to store execution result: {e}")

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an active order."""
        try:
            # Wrap synchronous SDK call
            await asyncio.to_thread(self.trade_context.cancel_order, order_id)

            # Remove from active orders
            self._active_orders.pop(order_id, None)

            logger.info(f"Order {order_id} cancelled")
            return True

        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def modify_order(
        self,
        order_id: str,
        new_quantity: Optional[int] = None,
        new_price: Optional[float] = None
    ) -> bool:
        """Modify an existing order."""
        try:
            # Note: Longport SDK uses replace_order, not modify_order
            # Wrap synchronous SDK call
            await asyncio.to_thread(
                self.trade_context.replace_order,
                order_id,
                new_quantity,
                Decimal(str(new_price)) if new_price else None
            )

            logger.info(f"Order {order_id} modified")
            return True

        except Exception as e:
            logger.error(f"Failed to modify order {order_id}: {e}")
            return False

    def calculate_optimal_slice_size(
        self,
        total_quantity: int,
        avg_volume: int,
        urgency: int
    ) -> int:
        """Calculate optimal order slice size."""
        # Base slice size on percentage of average volume
        base_pct = 0.01 if urgency < 5 else 0.02 if urgency < 8 else 0.05

        slice_size = int(avg_volume * base_pct)

        # Ensure reasonable bounds
        min_slice = 100
        max_slice = total_quantity // 3  # At least 3 slices

        return max(min_slice, min(slice_size, max_slice))
