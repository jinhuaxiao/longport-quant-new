"""
凯利公式仓位管理器

实现基于历史统计的凯利公式仓位计算，用于优化资金分配和仓位管理。

核心功能：
1. 计算历史胜率和盈亏比
2. 使用凯利公式计算最优仓位
3. 应用保守系数（50% Kelly）降低风险
4. 结合信号评分动态调整
"""

import logging
from decimal import Decimal
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
import os
from dataclasses import dataclass
import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class TradingStats:
    """交易统计数据"""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    max_win: float
    max_loss: float

    def __str__(self):
        return (
            f"总交易: {self.total_trades}, "
            f"胜率: {self.win_rate:.1%}, "
            f"盈亏比: {self.avg_win/abs(self.avg_loss) if self.avg_loss else 0:.2f}, "
            f"盈利因子: {self.profit_factor:.2f}"
        )


class KellyCalculator:
    """
    凯利公式仓位计算器

    凯利公式: f = (p * b - q) / b
    其中:
        f = 应投入的资金比例
        p = 胜率
        q = 1 - p (失败率)
        b = 平均盈利 / 平均亏损

    使用 50% Kelly 保守系数以降低波动和风险
    """

    def __init__(
        self,
        kelly_fraction: float = 0.5,  # 50% Kelly
        max_position_size: float = 0.25,  # 最大仓位 25%
        min_win_rate: float = 0.55,  # 最小胜率要求
        min_trades: int = 10,  # 最少交易次数
        lookback_days: int = 30  # 统计回溯天数
    ):
        """
        初始化凯利公式计算器

        Args:
            kelly_fraction: 凯利公式保守系数 (0.5 = 50% Kelly)
            max_position_size: 最大单笔仓位比例
            min_win_rate: 启用凯利公式的最小胜率
            min_trades: 启用凯利公式的最少交易次数
            lookback_days: 统计历史数据的回溯天数
        """
        # 从环境变量获取 PostgreSQL 连接字符串
        db_url = os.getenv('DATABASE_DSN', 'postgresql://postgres:jinhua@127.0.0.1:5432/longport_next_new')
        if db_url.startswith('postgresql+asyncpg://'):
            db_url = db_url.replace('postgresql+asyncpg://', 'postgresql://')
        self.db_url = db_url

        self.kelly_fraction = kelly_fraction
        self.max_position_size = max_position_size
        self.min_win_rate = min_win_rate
        self.min_trades = min_trades
        self.lookback_days = lookback_days
        self.pool = None

        logger.info(
            f"凯利公式计算器初始化: "
            f"db=PostgreSQL, "
            f"kelly_fraction={kelly_fraction}, "
            f"max_position={max_position_size:.1%}, "
            f"min_win_rate={min_win_rate:.1%}, "
            f"lookback_days={lookback_days}"
        )

    async def close(self):
        """关闭数据库连接池"""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.debug("✅ Kelly Calculator 数据库连接池已关闭")

    async def get_trading_stats(
        self,
        symbol: Optional[str] = None,
        market: Optional[str] = None
    ) -> Optional[TradingStats]:
        """
        从 PostgreSQL 数据库获取交易统计数据

        Args:
            symbol: 指定股票代码（None=全部）
            market: 指定市场 "HK" 或 "US"（None=全部）

        Returns:
            TradingStats 对象，如果数据不足返回 None
        """
        try:
            # 创建数据库连接
            if not self.pool:
                self.pool = await asyncpg.create_pool(
                    self.db_url,
                    min_size=1,
                    max_size=2,
                    command_timeout=8
                )

            # 计算时间范围
            cutoff_date = datetime.now() - timedelta(days=self.lookback_days)

            # 构建查询条件
            conditions = ["exit_time IS NOT NULL", "status IN ('hit_stop_loss', 'hit_take_profit', 'closed')"]
            params = [cutoff_date]
            param_idx = 2

            if symbol:
                conditions.append(f"symbol = ${param_idx}")
                params.append(symbol)
                param_idx += 1

            if market:
                conditions.append(f"symbol LIKE ${ param_idx}")
                params.append(f"%.{market}")
                param_idx += 1

            where_clause = " AND ".join(conditions)

            # 查询已平仓交易的盈亏数据
            query = f"""
                SELECT
                    symbol,
                    entry_price,
                    exit_price,
                    pnl,
                    exit_time
                FROM position_stops
                WHERE exit_time >= $1 AND {where_clause}
                ORDER BY exit_time DESC
            """

            async with self.pool.acquire() as conn:
                trades = await conn.fetch(query, *params)

            if len(trades) < self.min_trades:
                logger.warning(
                    f"交易记录不足: {len(trades)} < {self.min_trades}, "
                    f"symbol={symbol}, market={market}"
                )
                return None

            # 计算每笔交易的盈亏
            wins = []
            losses = []

            for trade in trades:
                entry_price = float(trade['entry_price']) if trade['entry_price'] else 0
                exit_price = float(trade['exit_price']) if trade['exit_price'] else 0

                # 跳过价格数据缺失的记录
                if not entry_price or not exit_price:
                    continue

                # 计算盈亏百分比（假设都是做多）
                pnl_pct = (exit_price - entry_price) / entry_price

                if pnl_pct > 0:
                    wins.append(pnl_pct)
                elif pnl_pct < 0:
                    losses.append(abs(pnl_pct))

            # 检查是否有足够的盈亏数据
            if not wins and not losses:
                logger.warning("没有有效的盈亏数据")
                return None

            total_trades = len(wins) + len(losses)
            winning_trades = len(wins)
            losing_trades = len(losses)

            # 计算胜率
            win_rate = winning_trades / total_trades if total_trades > 0 else 0

            # 计算平均盈利和平均亏损
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(losses) / len(losses) if losses else 0

            # 计算盈利因子（总盈利 / 总亏损）
            total_win = sum(wins)
            total_loss = sum(losses)
            profit_factor = total_win / total_loss if total_loss > 0 else float('inf')

            # 最大单笔盈利和亏损
            max_win = max(wins) if wins else 0
            max_loss = max(losses) if losses else 0

            stats = TradingStats(
                total_trades=total_trades,
                winning_trades=winning_trades,
                losing_trades=losing_trades,
                win_rate=win_rate,
                avg_win=avg_win,
                avg_loss=avg_loss,
                profit_factor=profit_factor,
                max_win=max_win,
                max_loss=max_loss
            )

            logger.info(
                f"交易统计 (symbol={symbol}, market={market}): {stats}"
            )

            return stats

        except Exception as e:
            logger.error(f"获取交易统计失败: {e}", exc_info=True)
            return None

    def calculate_kelly_position(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
        total_capital: float,
        signal_score: Optional[float] = None,
        regime: Optional[str] = None
    ) -> Tuple[float, Dict[str, float]]:
        """
        使用凯利公式计算最优仓位大小

        Args:
            win_rate: 胜率 (0-1)
            avg_win: 平均盈利百分比
            avg_loss: 平均亏损百分比（正数）
            total_capital: 总资金
            signal_score: 信号评分 (0-100)，用于动态调整
            regime: 市场状态 "BULL"/"BEAR"/"RANGE"

        Returns:
            (建议仓位金额, 详细信息字典)
        """
        try:
            # 参数验证
            if avg_loss <= 0:
                logger.warning("平均亏损为0，无法计算凯利公式")
                return 0, {"error": "avg_loss_zero"}

            if win_rate < self.min_win_rate:
                logger.warning(
                    f"胜率不足: {win_rate:.1%} < {self.min_win_rate:.1%}"
                )
                return 0, {"error": "win_rate_too_low", "win_rate": win_rate}

            # 计算盈亏比 b
            b = avg_win / avg_loss

            # 凯利公式: f = (p * b - q) / b
            q = 1 - win_rate
            kelly_full = (win_rate * b - q) / b

            # 应用保守系数
            kelly_adjusted = kelly_full * self.kelly_fraction

            # 限制最大仓位
            kelly_capped = min(kelly_adjusted, self.max_position_size)

            # 确保非负
            kelly_final = max(kelly_capped, 0)

            # 市场状态调整
            regime_multiplier = 1.0
            if regime == "BULL":
                regime_multiplier = 1.0  # 牛市：正常仓位
            elif regime == "RANGE":
                regime_multiplier = 0.8  # 震荡：降低 20%
            elif regime == "BEAR":
                regime_multiplier = 0.5  # 熊市：降低 50%

            kelly_final *= regime_multiplier

            # 信号评分调整（可选）
            signal_multiplier = 1.0
            if signal_score is not None:
                if signal_score >= 80:
                    signal_multiplier = 1.2  # 高分信号：增加 20%
                elif signal_score >= 70:
                    signal_multiplier = 1.1  # 良好信号：增加 10%
                elif signal_score >= 60:
                    signal_multiplier = 1.0  # 一般信号：正常
                else:
                    signal_multiplier = 0.8  # 低分信号：降低 20%

            kelly_final *= signal_multiplier

            # 再次限制最大仓位
            kelly_final = min(kelly_final, self.max_position_size)

            # 计算建议仓位金额
            position_size = total_capital * kelly_final

            # 详细信息
            info = {
                "kelly_full": kelly_full,
                "kelly_fraction": self.kelly_fraction,
                "kelly_adjusted": kelly_adjusted,
                "kelly_capped": kelly_capped,
                "regime_multiplier": regime_multiplier,
                "signal_multiplier": signal_multiplier,
                "kelly_final": kelly_final,
                "position_size": position_size,
                "position_pct": kelly_final,
                "win_rate": win_rate,
                "avg_win": avg_win,
                "avg_loss": avg_loss,
                "profit_loss_ratio": b
            }

            logger.info(
                f"凯利公式计算: "
                f"胜率={win_rate:.1%}, "
                f"盈亏比={b:.2f}, "
                f"完整凯利={kelly_full:.1%}, "
                f"调整后={kelly_final:.1%}, "
                f"仓位=${position_size:,.0f}"
            )

            return position_size, info

        except Exception as e:
            logger.error(f"凯利公式计算失败: {e}", exc_info=True)
            return 0, {"error": str(e)}

    async def get_recommended_position(
        self,
        total_capital: float,
        signal_score: float,
        symbol: Optional[str] = None,
        market: Optional[str] = None,
        regime: Optional[str] = None,
        fallback_pct: float = 0.10  # 默认回退到 10%
    ) -> Tuple[float, Dict]:
        """
        获取推荐的仓位大小（结合历史统计和凯利公式）

        Args:
            total_capital: 总资金
            signal_score: 信号评分
            symbol: 股票代码（用于个股统计）
            market: 市场 "HK" 或 "US"
            regime: 市场状态
            fallback_pct: 数据不足时的回退比例

        Returns:
            (建议仓位金额, 详细信息)
        """
        # 1. 尝试获取个股统计
        stats = None
        if symbol:
            stats = await self.get_trading_stats(symbol=symbol)

        # 2. 如果个股数据不足，尝试市场统计
        if not stats and market:
            stats = await self.get_trading_stats(market=market)

        # 3. 如果仍然没有数据，使用全市场统计
        if not stats:
            stats = await self.get_trading_stats()

        # 4. 如果完全没有历史数据，使用回退策略
        if not stats:
            logger.warning(
                f"无历史统计数据，使用回退策略: {fallback_pct:.1%}"
            )
            fallback_size = total_capital * fallback_pct

            # 根据信号评分调整
            if signal_score >= 80:
                fallback_size *= 1.2
            elif signal_score >= 70:
                fallback_size *= 1.1
            elif signal_score < 60:
                fallback_size *= 0.8

            return fallback_size, {
                "method": "fallback",
                "fallback_pct": fallback_pct,
                "position_size": fallback_size
            }

        # 5. 使用凯利公式计算
        position_size, info = self.calculate_kelly_position(
            win_rate=stats.win_rate,
            avg_win=stats.avg_win,
            avg_loss=stats.avg_loss,
            total_capital=total_capital,
            signal_score=signal_score,
            regime=regime
        )

        # 6. 如果凯利公式返回0（胜率不足等），使用保守回退
        if position_size == 0:
            logger.warning("凯利公式返回0，使用保守回退策略")
            position_size = total_capital * (fallback_pct * 0.5)
            info["method"] = "conservative_fallback"
        else:
            info["method"] = "kelly"

        # 7. 添加统计信息
        info["stats"] = {
            "total_trades": stats.total_trades,
            "win_rate": stats.win_rate,
            "avg_win": stats.avg_win,
            "avg_loss": stats.avg_loss,
            "profit_factor": stats.profit_factor
        }

        return position_size, info


# 便捷函数
def calculate_kelly_position_simple(
    win_rate: float,
    profit_loss_ratio: float,
    total_capital: float,
    kelly_fraction: float = 0.5,
    max_position: float = 0.25
) -> float:
    """
    简化的凯利公式计算（独立函数）

    Args:
        win_rate: 胜率 (0-1)
        profit_loss_ratio: 盈亏比（平均盈利/平均亏损）
        total_capital: 总资金
        kelly_fraction: 保守系数
        max_position: 最大仓位比例

    Returns:
        建议仓位金额
    """
    if profit_loss_ratio <= 0:
        return 0

    q = 1 - win_rate
    kelly = (win_rate * profit_loss_ratio - q) / profit_loss_ratio
    kelly_adjusted = kelly * kelly_fraction
    kelly_final = min(max(kelly_adjusted, 0), max_position)

    return total_capital * kelly_final
