"""
时区轮动资金管理器

实现跨时区（港股/美股）的智能资金轮动管理，最大化资金利用效率。

核心功能：
1. 优先级策略：优先满足高分信号，不限制市场
2. 自动识别弱势持仓并计算可释放资金
3. 港股收盘前为美股释放资金，美股收盘前为港股释放资金
4. 动态调整两个市场的资金分配比例
"""

import logging
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CapitalAllocation:
    """资金分配结果"""
    total_capital: float
    allocated_capital: float
    reserved_capital: float
    available_for_signals: float
    releasable_from_positions: float
    allocation_method: str
    details: Dict

    def __str__(self):
        return (
            f"总资金: ${self.total_capital:,.0f}, "
            f"已分配: ${self.allocated_capital:,.0f}, "
            f"可用: ${self.available_for_signals:,.0f}, "
            f"可释放: ${self.releasable_from_positions:,.0f}"
        )


@dataclass
class RotatablePosition:
    """可轮换持仓"""
    symbol: str
    market_value: float
    rotation_score: float  # 轮换评分（越低越应该卖出）
    profit_pct: float
    holding_hours: float
    technical_weakness: int  # 技术面弱势评分
    reason: str  # 建议卖出的原因

    def __str__(self):
        return (
            f"{self.symbol}: "
            f"市值=${self.market_value:,.0f}, "
            f"评分={self.rotation_score:.0f}, "
            f"盈亏={self.profit_pct:+.1%}, "
            f"原因={self.reason}"
        )


class TimeZoneCapitalManager:
    """
    时区轮动资金管理器

    策略说明：
    1. 优先级策略：高分信号（≥70）优先获得资金，不限市场
    2. 港股收盘前（15:30-16:00）：评估港股持仓，为美股释放资金
    3. 美股收盘前（15:00-16:00 ET）：评估美股持仓，为港股释放资金
    4. 自动识别弱势持仓（轮换评分 < 40）
    """

    def __init__(
        self,
        weak_position_threshold: int = 40,  # 弱势持仓评分阈值
        max_rotation_pct: float = 0.30,  # 单次最多轮换 30% 持仓
        min_profit_for_rotation: float = -0.10,  # 亏损超过 10% 优先轮换
        strong_position_threshold: int = 70,  # 强势持仓保护阈值
        min_holding_hours: float = 0.5,  # 最短持有时间（小时）
    ):
        """
        初始化时区资金管理器

        Args:
            weak_position_threshold: 轮换评分低于此值视为弱势
            max_rotation_pct: 单次最多轮换的持仓市值比例
            min_profit_for_rotation: 亏损超过此值优先轮换
            strong_position_threshold: 评分高于此值不轮换
            min_holding_hours: 最短持有时间（避免频繁交易）
        """
        self.weak_position_threshold = weak_position_threshold
        self.max_rotation_pct = max_rotation_pct
        self.min_profit_for_rotation = min_profit_for_rotation
        self.strong_position_threshold = strong_position_threshold
        self.min_holding_hours = min_holding_hours

        logger.info(
            f"时区资金管理器初始化: "
            f"弱势阈值={weak_position_threshold}, "
            f"最大轮换={max_rotation_pct:.1%}, "
            f"强势保护={strong_position_threshold}"
        )

    def calculate_rotation_score(
        self,
        position: Dict,
        current_price: float,
        technical_indicators: Dict,
        regime: Optional[str] = None
    ) -> Tuple[float, str]:
        """
        计算持仓轮换评分

        评分越低，越应该卖出为其他信号让路
        基准分：50

        Args:
            position: 持仓信息
            current_price: 当前价格
            technical_indicators: 技术指标
            regime: 市场状态

        Returns:
            (轮换评分, 原因说明)
        """
        score = 50  # 基准分
        reasons = []

        # 1. 盈亏影响（-50 to +30）
        entry_price = position.get("average_cost", position.get("entry_price", 0))
        if entry_price > 0:
            profit_pct = (current_price - entry_price) / entry_price

            if profit_pct < -0.10:
                score -= 30
                reasons.append(f"亏损>{10:.0%}")
            elif profit_pct < -0.05:
                score -= 20
                reasons.append(f"亏损>{5:.0%}")
            elif profit_pct < 0:
                score -= 10
                reasons.append("亏损中")
            elif profit_pct > 0.20:
                score += 30
                reasons.append(f"盈利>{20:.0%}")
            elif profit_pct > 0.10:
                score += 20
                reasons.append(f"盈利>{10:.0%}")
            elif profit_pct > 0.05:
                score += 10
                reasons.append(f"盈利>{5:.0%}")

        # 2. 持有时间（-10 to +10）
        entry_time = position.get("entry_time")
        if entry_time:
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)
            holding_hours = (datetime.now() - entry_time).total_seconds() / 3600

            if holding_hours < self.min_holding_hours:
                score += 10  # 刚开仓，保留
                reasons.append("刚开仓")
            elif holding_hours > 24:
                score -= 10  # 持有过久
                reasons.append("持有>24h")

        # 3. 技术指标（-40 to 0）
        technical_weakness = 0

        # RSI 超买
        rsi = technical_indicators.get("rsi")
        if rsi and rsi > 70:
            technical_weakness += 15
            reasons.append("RSI超买")

        # MACD 死叉
        macd_signal = technical_indicators.get("macd_signal")
        if macd_signal == "BEARISH_CROSS":
            technical_weakness += 15
            reasons.append("MACD死叉")

        # 跌破均线
        if technical_indicators.get("below_sma20"):
            technical_weakness += 10
            reasons.append("破SMA20")

        if technical_indicators.get("below_sma50"):
            technical_weakness += 10
            reasons.append("破SMA50")

        score -= technical_weakness

        # 4. 市场状态调整
        if regime == "BEAR":
            score -= 15  # 熊市：更激进卖出
            reasons.append("熊市")
        elif regime == "BULL":
            score += 10  # 牛市：更耐心持有
            reasons.append("牛市")

        # 生成原因说明
        reason_str = ", ".join(reasons) if reasons else "正常"

        logger.debug(
            f"{position.get('symbol')}: 轮换评分={score:.0f}, 原因={reason_str}"
        )

        return score, reason_str

    def identify_rotatable_positions(
        self,
        positions: List[Dict],
        quotes: Dict[str, Dict],
        technical_data: Dict[str, Dict],
        regime: Optional[str] = None,
        target_market: Optional[str] = None
    ) -> List[RotatablePosition]:
        """
        识别可轮换的持仓（弱势持仓）

        Args:
            positions: 当前持仓列表
            quotes: 实时行情数据 {symbol: quote}
            technical_data: 技术指标数据 {symbol: indicators}
            regime: 市场状态
            target_market: 目标市场（需要释放资金给哪个市场）

        Returns:
            可轮换持仓列表，按评分从低到高排序
        """
        rotatable = []

        for position in positions:
            symbol = position.get("symbol")

            # 如果指定了目标市场，只评估其他市场的持仓
            if target_market:
                if target_market == "US" and not symbol.endswith(".US"):
                    continue  # 需要为美股释放资金，但这是港股持仓，跳过
                if target_market == "HK" and not symbol.endswith(".HK"):
                    continue

            # 获取当前价格
            quote = quotes.get(symbol, {})
            current_price = quote.get("last_done", quote.get("price", 0))

            if current_price <= 0:
                logger.warning(f"{symbol}: 无法获取当前价格，跳过")
                continue

            # 获取技术指标
            indicators = technical_data.get(symbol, {})

            # 计算轮换评分
            rotation_score, reason = self.calculate_rotation_score(
                position=position,
                current_price=current_price,
                technical_indicators=indicators,
                regime=regime
            )

            # 计算盈亏
            entry_price = position.get("average_cost", position.get("entry_price", 0))
            profit_pct = 0
            if entry_price > 0:
                profit_pct = (current_price - entry_price) / entry_price

            # 计算持有时间
            entry_time = position.get("entry_time")
            holding_hours = 0
            if entry_time:
                if isinstance(entry_time, str):
                    entry_time = datetime.fromisoformat(entry_time)
                holding_hours = (datetime.now() - entry_time).total_seconds() / 3600

            # 计算市值
            quantity = position.get("quantity", 0)
            market_value = current_price * quantity

            # 判断是否应该轮换
            should_rotate = False

            # 条件1：评分低于阈值
            if rotation_score < self.weak_position_threshold:
                should_rotate = True

            # 条件2：严重亏损（>10%）
            if profit_pct < self.min_profit_for_rotation:
                should_rotate = True
                reason = f"严重亏损{profit_pct:.1%}"

            # 保护条件：强势持仓不轮换
            if rotation_score >= self.strong_position_threshold:
                should_rotate = False

            # 保护条件：刚开仓不轮换
            if holding_hours < self.min_holding_hours:
                should_rotate = False

            if should_rotate:
                rotatable.append(RotatablePosition(
                    symbol=symbol,
                    market_value=market_value,
                    rotation_score=rotation_score,
                    profit_pct=profit_pct,
                    holding_hours=holding_hours,
                    technical_weakness=indicators.get("weakness_score", 0),
                    reason=reason
                ))

        # 按评分从低到高排序（最弱的在前）
        rotatable.sort(key=lambda x: x.rotation_score)

        logger.info(
            f"识别出 {len(rotatable)} 个可轮换持仓 "
            f"(总持仓={len(positions)})"
        )

        for pos in rotatable[:5]:  # 显示前5个
            logger.info(f"  - {pos}")

        return rotatable

    def calculate_releasable_capital(
        self,
        rotatable_positions: List[RotatablePosition],
        total_position_value: float
    ) -> Tuple[float, List[RotatablePosition]]:
        """
        计算可释放的资金（考虑最大轮换比例限制）

        Args:
            rotatable_positions: 可轮换持仓列表
            total_position_value: 总持仓市值

        Returns:
            (可释放资金, 建议卖出的持仓列表)
        """
        max_releasable = total_position_value * self.max_rotation_pct

        releasable = 0
        to_sell = []

        for pos in rotatable_positions:
            if releasable >= max_releasable:
                break

            # 保守估算：只计算 80% 市值（考虑卖出滑点）
            estimated_proceeds = pos.market_value * 0.8

            releasable += estimated_proceeds
            to_sell.append(pos)

        logger.info(
            f"可释放资金: ${releasable:,.0f} "
            f"(最大限制=${max_releasable:,.0f}, "
            f"需卖出{len(to_sell)}个持仓)"
        )

        return releasable, to_sell

    def allocate_capital_priority_based(
        self,
        total_capital: float,
        available_cash: float,
        current_positions: List[Dict],
        pending_signals: List[Dict],
        quotes: Dict[str, Dict],
        technical_data: Dict[str, Dict],
        regime: Optional[str] = None
    ) -> CapitalAllocation:
        """
        基于优先级的资金分配策略

        策略：优先满足高分信号（≥70），不限制市场

        Args:
            total_capital: 总资金（净资产）
            available_cash: 当前可用现金
            current_positions: 当前持仓
            pending_signals: 待处理信号列表
            quotes: 实时行情
            technical_data: 技术指标
            regime: 市场状态

        Returns:
            CapitalAllocation 对象
        """
        # 1. 计算已分配资金（当前持仓）
        allocated_capital = sum(
            pos.get("market_value", 0) for pos in current_positions
        )

        # 2. 计算预留资金（基于市场状态）
        reserve_pct = 0.15  # 默认预留 15%
        if regime == "BEAR":
            reserve_pct = 0.50  # 熊市预留 50%
        elif regime == "RANGE":
            reserve_pct = 0.30  # 震荡预留 30%

        reserved_capital = total_capital * reserve_pct

        # 3. 筛选高分信号
        high_score_signals = [
            s for s in pending_signals
            if s.get("score", 0) >= 70
        ]

        # 4. 计算高分信号所需资金
        required_for_signals = 0
        for signal in high_score_signals:
            # 估算每个信号需要的资金（假设10%仓位）
            required_for_signals += total_capital * 0.10

        # 5. 判断是否需要释放持仓资金
        available_for_signals = available_cash - reserved_capital
        shortage = max(0, required_for_signals - available_for_signals)

        releasable_capital = 0
        rotatable_positions = []

        if shortage > 0:
            logger.info(
                f"资金不足: 需要${required_for_signals:,.0f}, "
                f"可用${available_for_signals:,.0f}, "
                f"缺口=${shortage:,.0f}"
            )

            # 识别可轮换持仓
            rotatable_positions = self.identify_rotatable_positions(
                positions=current_positions,
                quotes=quotes,
                technical_data=technical_data,
                regime=regime
            )

            # 计算可释放资金
            total_position_value = allocated_capital
            releasable_capital, _ = self.calculate_releasable_capital(
                rotatable_positions=rotatable_positions,
                total_position_value=total_position_value
            )

        # 6. 构建分配结果
        allocation = CapitalAllocation(
            total_capital=total_capital,
            allocated_capital=allocated_capital,
            reserved_capital=reserved_capital,
            available_for_signals=available_for_signals + releasable_capital,
            releasable_from_positions=releasable_capital,
            allocation_method="priority_based",
            details={
                "regime": regime,
                "reserve_pct": reserve_pct,
                "high_score_signals": len(high_score_signals),
                "required_for_signals": required_for_signals,
                "shortage": shortage,
                "rotatable_positions": len(rotatable_positions)
            }
        )

        logger.info(f"资金分配结果: {allocation}")

        return allocation

    def should_trigger_pre_close_rotation(
        self,
        current_time: datetime,
        market: str
    ) -> bool:
        """
        判断是否应该触发收盘前轮换

        Args:
            current_time: 当前时间
            market: 市场 "HK" 或 "US"

        Returns:
            是否应该触发
        """
        hour = current_time.hour
        minute = current_time.minute

        if market == "HK":
            # 港股收盘前 30 分钟（15:30-16:00）
            return (hour == 15 and minute >= 30) or (hour == 16 and minute == 0)

        elif market == "US":
            # 美股收盘前 1 小时（15:00-16:00 ET）
            # 注意：这里假设服务器时间是 UTC，需要根据实际情况调整
            return hour == 15 or (hour == 16 and minute == 0)

        return False


# 便捷函数
def calculate_simple_rotation_score(
    profit_pct: float,
    holding_hours: float,
    technical_weakness: int = 0
) -> float:
    """
    简化的轮换评分计算

    Args:
        profit_pct: 盈亏百分比
        holding_hours: 持有时间（小时）
        technical_weakness: 技术面弱势评分

    Returns:
        轮换评分（越低越应卖出）
    """
    score = 50

    # 盈亏影响
    if profit_pct < -0.10:
        score -= 30
    elif profit_pct < -0.05:
        score -= 20
    elif profit_pct < 0:
        score -= 10
    elif profit_pct > 0.20:
        score += 30
    elif profit_pct > 0.10:
        score += 20
    elif profit_pct > 0.05:
        score += 10

    # 持有时间
    if holding_hours < 1:
        score += 10
    elif holding_hours > 24:
        score -= 10

    # 技术面
    score -= technical_weakness

    return score
