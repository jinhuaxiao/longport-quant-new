#!/usr/bin/env python3
"""智能持仓轮换系统 - 解决满仓时强信号无法执行的问题"""

import asyncio
from datetime import datetime, time
from zoneinfo import ZoneInfo
from loguru import logger
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings
from longport_quant.execution.client import LongportTradingClient
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.persistence.order_manager import OrderManager
from longport_quant.persistence.stop_manager import StopLossManager
from longport_quant.features.technical_indicators import TechnicalIndicators
from longport_quant.persistence.position_manager import RedisPositionManager
from longport import openapi
import numpy as np


class SmartPositionRotator:
    """
    智能持仓轮换系统

    解决问题：
    - 满仓时强信号无法执行
    - 自动识别最弱持仓
    - 智能平仓为强信号腾出空间
    """

    def __init__(self):
        self.settings = get_settings()
        self.order_manager = OrderManager()
        self.stop_manager = StopLossManager()
        self.position_manager = RedisPositionManager(
            redis_url=self.settings.redis_url,
            key_prefix="trading"
        )
        self.max_positions = 999  # 不限制持仓数量（与主脚本保持一致）
        self.beijing_tz = ZoneInfo("Asia/Shanghai")  # 北京时区
        # 刚建仓的标的至少持有10分钟后才允许轮换，避免频繁买卖
        self.min_hold_seconds = 600

    async def evaluate_position_strength(self, symbol: str, position: Dict,
                                        quote_client: QuoteDataClient) -> float:
        """
        评估持仓强度（0-100分）

        评分维度：
        1. 盈亏状况 (30分)
        2. 技术指标 (30分)
        3. 持仓时间 (20分)
        4. 成交量 (10分)
        5. 止损距离 (10分)
        """
        score = 0.0

        try:
            # 获取实时行情
            quotes = await quote_client.get_realtime_quote([symbol])
            if not quotes:
                return 50.0  # 默认中等分数

            quote = quotes[0]
            current_price = float(quote.last_done)
            entry_price = position["cost"]

            # 1. 盈亏评分 (30分)
            pnl_pct = (current_price / entry_price - 1) * 100
            if pnl_pct >= 10:
                score += 30  # 大幅盈利
            elif pnl_pct >= 5:
                score += 25  # 中等盈利
            elif pnl_pct >= 2:
                score += 20  # 小幅盈利
            elif pnl_pct >= 0:
                score += 15  # 微盈
            elif pnl_pct >= -3:
                score += 10  # 小幅亏损
            elif pnl_pct >= -5:
                score += 5   # 中等亏损
            else:
                score += 0   # 大幅亏损

            # 2. 技术指标评分 (30分)
            tech_score = await self._calculate_technical_score(symbol, quote_client)
            score += tech_score

            # 3. 持仓时间评分 (20分) - 越短越容易被替换
            # 假设新持仓更容易被替换
            days_held = position.get("days_held", 0)
            if days_held >= 30:
                score += 20  # 长期持仓
            elif days_held >= 14:
                score += 15  # 中期持仓
            elif days_held >= 7:
                score += 10  # 短期持仓
            elif days_held >= 3:
                score += 5   # 新持仓
            else:
                score += 0   # 刚买入

            # 4. 成交量评分 (10分)
            volume = float(quote.volume) if quote.volume else 0
            avg_volume = float(quote.avg_volume) if hasattr(quote, 'avg_volume') else volume
            if avg_volume > 0:
                volume_ratio = volume / avg_volume
                if volume_ratio >= 2:
                    score += 10  # 放量
                elif volume_ratio >= 1.5:
                    score += 7
                elif volume_ratio >= 1:
                    score += 5
                else:
                    score += 2  # 缩量

            # 5. 止损距离评分 (10分)
            stop_data = await self.stop_manager.get_stop_for_symbol(symbol)
            if stop_data:
                stop_loss = stop_data["stop_loss"]
                stop_distance_pct = abs((current_price - stop_loss) / current_price * 100)
                if stop_distance_pct >= 10:
                    score += 10  # 止损距离远
                elif stop_distance_pct >= 5:
                    score += 7
                elif stop_distance_pct >= 3:
                    score += 5
                else:
                    score += 2  # 接近止损

            logger.info(
                f"  {symbol}: {score:.1f}分 "
                f"(盈亏{pnl_pct:+.1f}%, 持仓{days_held}天)"
            )

            return min(100, score)

        except Exception as e:
            logger.error(f"评估 {symbol} 失败: {e}")
            return 50.0

    async def _calculate_technical_score(self, symbol: str,
                                        quote_client: QuoteDataClient) -> float:
        """计算技术指标评分 (0-30分)"""
        try:
            # 获取历史数据（使用by_offset方法，支持count参数）
            candles = await quote_client.get_history_candles_by_offset(
                symbol=symbol,
                period=openapi.Period.Day,
                adjust_type=openapi.AdjustType.NoAdjust,
                forward=False,  # False表示向后查询历史数据
                count=60
            )

            if not candles or len(candles) < 30:
                return 15  # 默认中等分数

            closes = np.array([float(c.close) for c in candles])

            score = 0

            # RSI评分
            rsi = TechnicalIndicators.rsi(closes, 14)
            current_rsi = rsi[-1]

            if 40 <= current_rsi <= 60:
                score += 10  # RSI中性
            elif 30 <= current_rsi < 40:
                score += 15  # RSI超卖反弹
            elif 60 < current_rsi <= 70:
                score += 8   # RSI偏强
            elif current_rsi < 30:
                score += 12  # RSI深度超卖
            elif current_rsi > 70:
                score += 5   # RSI超买

            # MACD评分
            macd = TechnicalIndicators.macd(closes, 12, 26, 9)
            macd_hist = macd['histogram'][-1]

            if macd_hist > 0:
                score += 10  # MACD多头
            else:
                score += 5   # MACD空头

            # 均线评分
            sma_20 = TechnicalIndicators.sma(closes, 20)
            if closes[-1] > sma_20[-1]:
                score += 10  # 价格在均线上方
            else:
                score += 5   # 价格在均线下方

            return min(30, score)

        except Exception as e:
            logger.debug(f"计算技术评分失败: {e}")
            return 15

    def _is_market_open(self, symbol: str) -> bool:
        """
        检查市场是否开盘

        Args:
            symbol: 标的代码（如 1398.HK, AAPL.US, 300750.SZ）

        Returns:
            bool: 市场是否开盘
        """
        now = datetime.now(self.beijing_tz)
        weekday = now.weekday()  # 0=周一, 6=周日
        current_time = now.time()

        if symbol.endswith('.HK'):
            # 港股交易时间（北京时间）
            # 周一到周五: 9:30-12:00, 13:00-16:00
            if weekday >= 5:  # 周六或周日
                return False

            morning_start = time(9, 30)
            morning_end = time(12, 0)
            afternoon_start = time(13, 0)
            afternoon_end = time(16, 0)

            is_morning = morning_start <= current_time <= morning_end
            is_afternoon = afternoon_start <= current_time <= afternoon_end

            return is_morning or is_afternoon

        elif symbol.endswith('.US'):
            # 美股交易时间（北京时间）
            # 夏令时（3月第二个周日 - 11月第一个周日）: 21:30 - 次日04:00
            # 冬令时（11月第一个周日 - 次年3月第二个周日）: 22:30 - 次日05:00
            # 简化处理：使用 21:30 - 次日05:00（涵盖两种情况）

            # 美股周一到周五交易，对应北京时间周二到周六早上
            market_start = time(21, 30)
            market_end = time(5, 0)

            # 如果当前是晚上21:30之后，需要是周一到周五
            if current_time >= market_start:
                return weekday < 5  # 周一到周五
            # 如果当前是早上05:00之前，需要是周二到周六
            elif current_time <= market_end:
                return 0 < weekday < 6  # 周二到周六
            else:
                return False

        elif symbol.endswith('.SH') or symbol.endswith('.SZ'):
            # A股交易时间（北京时间）
            # 周一到周五: 9:30-11:30, 13:00-15:00
            if weekday >= 5:  # 周六或周日
                return False

            morning_start = time(9, 30)
            morning_end = time(11, 30)
            afternoon_start = time(13, 0)
            afternoon_end = time(15, 0)

            is_morning = morning_start <= current_time <= morning_end
            is_afternoon = afternoon_start <= current_time <= afternoon_end

            return is_morning or is_afternoon

        else:
            # 未知市场，默认返回True（不过滤）
            return True

    async def find_weakest_positions(self, positions: Dict,
                                    quote_client: QuoteDataClient,
                                    num_positions: int = 1) -> List[Tuple[str, float]]:
        """
        找出最弱的持仓

        返回: [(symbol, score), ...] 按分数从低到高排序
        """
        position_scores = []

        logger.info("\n📊 评估所有持仓强度...")

        for symbol, position in positions.items():
            score = await self.evaluate_position_strength(
                symbol, position, quote_client
            )
            position_scores.append((symbol, score))

        # 按分数排序（低分优先）
        position_scores.sort(key=lambda x: x[1])

        weakest = position_scores[:num_positions]

        logger.info("\n🎯 最弱持仓:")
        for symbol, score in weakest:
            logger.warning(f"  {symbol}: {score:.1f}分 (建议替换)")

        return weakest

    async def compare_signal_strength(self, new_signal: Dict,
                                     weakest_position_score: float) -> bool:
        """
        比较新信号与最弱持仓的强度

        返回: True如果新信号更强
        """
        # 新信号评分（基于信号强度和指标）
        signal_score = 0

        # 信号强度评分
        signal_strength = new_signal.get("strength", 0)
        signal_score += signal_strength * 30  # 最高30分

        # RSI评分
        rsi = new_signal.get("rsi", 50)
        if rsi <= 30:
            signal_score += 20  # 深度超卖
        elif rsi <= 40:
            signal_score += 15  # 超卖
        elif rsi <= 50:
            signal_score += 10
        else:
            signal_score += 5

        # MACD评分
        if new_signal.get("macd_golden_cross"):
            signal_score += 20
        elif new_signal.get("macd_histogram", 0) > 0:
            signal_score += 10
        else:
            signal_score += 5

        # 成交量评分
        volume_surge = new_signal.get("volume_surge", 1)
        if volume_surge >= 2:
            signal_score += 15
        elif volume_surge >= 1.5:
            signal_score += 10
        else:
            signal_score += 5

        # BB评分
        if new_signal.get("bb_squeeze"):
            signal_score += 15
        elif new_signal.get("price_below_lower", False):
            signal_score += 10
        else:
            signal_score += 5

        logger.info(
            f"\n📈 信号对比: "
            f"新信号{signal_score:.1f}分 vs 最弱持仓{weakest_position_score:.1f}分"
        )

        # 新信号需要比最弱持仓高出至少10分才替换
        return signal_score > weakest_position_score + 10

    async def execute_position_rotation(self, new_signal: Dict,
                                       trade_client: LongportTradingClient,
                                       quote_client: QuoteDataClient) -> bool:
        """
        执行持仓轮换

        返回: True如果成功腾出空间
        """
        try:
            # 获取当前持仓
            positions_resp = await trade_client.stock_positions()
            positions = {}

            for channel in positions_resp.channels:
                for pos in channel.positions:
                    positions[pos.symbol] = {
                        "quantity": float(pos.quantity) if pos.quantity else 0,  # 转换为float
                        "cost": float(pos.cost_price) if pos.cost_price else 0,
                        "days_held": 0  # 简化处理
                    }

            if len(positions) < self.max_positions:
                logger.info(f"✅ 有空仓位 ({len(positions)}/{self.max_positions})")
                return True

            logger.warning(
                f"⚠️ 满仓状态 ({len(positions)}/{self.max_positions})，"
                f"评估是否需要轮换..."
            )

            # 找出最弱持仓
            weakest = await self.find_weakest_positions(positions, quote_client, 1)
            if not weakest:
                return False

            weakest_symbol, weakest_score = weakest[0]

            # 比较信号强度
            if not await self.compare_signal_strength(new_signal, weakest_score):
                logger.info("❌ 新信号不足以替换现有持仓")
                return False

            # 执行平仓
            logger.warning(f"\n🔄 执行持仓轮换: 卖出 {weakest_symbol}")

            position = positions[weakest_symbol]
            # 使用trade_client的submit_order接口（字典格式）
            order_resp = await trade_client.submit_order({
                "symbol": weakest_symbol,
                "side": "SELL",
                "quantity": position["quantity"],
                "price": None,  # Market order
                "remark": "Position rotation - weak position"
            })

            logger.success(
                f"✅ 轮换平仓订单已提交:\n"
                f"  订单ID: {order_resp.get('order_id', 'N/A')}\n"
                f"  标的: {weakest_symbol}\n"
                f"  数量: {position['quantity']}股\n"
                f"  原因: 为更强信号腾出空间"
            )

            # 更新止损记录
            await self.stop_manager.update_stop_status(
                weakest_symbol, "rotated_out"
            )

            return True

        except Exception as e:
            logger.error(f"执行持仓轮换失败: {e}")
            return False

    async def try_free_up_funds(
        self,
        needed_amount: float,
        new_signal: Dict,
        trade_client: LongportTradingClient,
        quote_client: QuoteDataClient,
        score_threshold: int = 15
    ) -> Tuple[bool, float, List[Dict[str, float]]]:
        """
        尝试释放指定金额的资金（可卖出多个弱势持仓）

        Args:
            needed_amount: 需要释放的资金量（美元）
            new_signal: 新信号数据（包含symbol, score等）
            trade_client: 交易客户端
            quote_client: 行情客户端
            score_threshold: 评分阈值差（默认15分）

        Returns:
            (成功与否, 实际释放的资金量, 卖出明细列表)
        """
        try:
            logger.info(f"\n💰 尝试释放资金: 需要 ${needed_amount:,.2f}")
            await self.position_manager.connect()

            # 1. 获取当前持仓
            positions_resp = await trade_client.stock_positions()
            positions: Dict[str, Dict[str, float]] = {}

            for channel in positions_resp.channels:
                for pos in channel.positions:
                    quantity = float(pos.quantity) if pos.quantity else 0
                    cost_price = float(pos.cost_price) if pos.cost_price else 0
                    market_value = quantity * cost_price

                    positions[pos.symbol] = {
                        "quantity": quantity,
                        "cost": cost_price,
                        "market_value": market_value,
                        "days_held": 0
                    }

            if not positions:
                logger.warning("⚠️ 没有持仓可以轮换")
                return False, 0.0, []

            # 2. 评估所有持仓强度
            logger.info(f"\n📊 评估所有持仓强度...")
            position_scores: List[Tuple[str, float, Dict]] = []

            for symbol, position in positions.items():
                score = await self.evaluate_position_strength(
                    symbol, position, quote_client
                )
                position_scores.append((symbol, score, position))

            # 按分数从低到高排序
            position_scores.sort(key=lambda x: x[1])

            # 3. 计算新信号评分和币种
            new_signal_score = new_signal.get('score', 0)
            new_signal_symbol = new_signal.get('symbol', 'N/A')
            logger.info(f"\n🎯 新信号评分: {new_signal_score}分 ({new_signal_symbol})")

            if new_signal_symbol.endswith(('.HK', '.SH', '.SZ')):
                new_currency = 'HKD'
            elif new_signal_symbol.endswith('.US'):
                new_currency = 'USD'
            else:
                new_currency = None

            if new_currency:
                logger.info(f"   需要币种: {new_currency}")

            # 4. 逐个卖出弱势持仓，直到资金足够
            total_freed = 0.0
            sold_positions: List[Dict[str, float]] = []

            for symbol, pos_score, position in position_scores:
                score_diff = new_signal_score - pos_score

                if score_diff < score_threshold:
                    logger.info(
                        f"  ⏭️ {symbol}: 评分{pos_score:.1f}分，"
                        f"与新信号差距{score_diff:.1f}分 < {score_threshold}分，保留"
                    )
                    continue

                if new_currency:
                    if symbol.endswith(('.HK', '.SH', '.SZ')):
                        pos_currency = 'HKD'
                    elif symbol.endswith('.US'):
                        pos_currency = 'USD'
                    else:
                        pos_currency = None

                    if pos_currency and pos_currency != new_currency:
                        logger.info(
                            f"  ⏭️ {symbol}: 币种不匹配（{pos_currency} ≠ {new_currency}），跳过"
                        )
                        continue

                if not self._is_market_open(symbol):
                    logger.info(f"  ⏭️ {symbol}: 市场休市，无法卖出")
                    continue

                hold_seconds = None
                try:
                    detail = await self.position_manager.get_position_detail(symbol)
                except Exception as e:
                    logger.debug(f"  ⚠️ 获取持仓详情失败，继续默认流程: {e}")
                    detail = None

                if detail and detail.get("added_at"):
                    try:
                        added_at = datetime.fromisoformat(detail["added_at"])
                        hold_seconds = (datetime.now(self.beijing_tz) - added_at).total_seconds()
                    except Exception as parse_err:
                        logger.debug(f"  ⚠️ 解析持仓时间失败: {parse_err}")
                        hold_seconds = None

                if hold_seconds is not None and hold_seconds < self.min_hold_seconds:
                    logger.info(
                        f"  ⏭️ {symbol}: 持仓仅 {hold_seconds/60:.1f} 分钟，"
                        f"未达到智能轮换最短持有 {self.min_hold_seconds/60:.1f} 分钟，保留"
                    )
                    continue

                if symbol == new_signal_symbol:
                    logger.info(
                        f"  ⏭️ {symbol}: 是新信号标的，跳过（避免先卖后买浪费手续费）\n"
                        f"     说明：已持仓评分{pos_score:.1f}分，新信号评分{new_signal_score}分，"
                        f"这是加仓场景，不应卖出后再买入"
                    )
                    continue

                market_value = position["market_value"]

                logger.warning(
                    f"  🔄 {symbol}: 评分{pos_score:.1f}分，"
                    f"新信号{new_signal_score}分，差距{score_diff:.1f}分 ≥ {score_threshold}分"
                )
                logger.info(f"     预计释放资金: ${market_value:,.2f}")

                try:
                    quotes = await quote_client.get_realtime_quote([symbol])
                    if quotes and len(quotes) > 0:
                        current_price = float(quotes[0].last_done)
                    else:
                        current_price = position["cost"]

                    order_resp = await trade_client.submit_order({
                        "symbol": symbol,
                        "side": "SELL",
                        "quantity": position["quantity"],
                        "price": current_price,
                        "remark": f"Smart rotation: free up ${needed_amount:.0f} for {new_signal.get('symbol', 'N/A')}"
                    })

                    freed_amount = position["quantity"] * current_price
                    total_freed += freed_amount
                    sold_positions.append({
                        "symbol": symbol,
                        "order_id": order_resp.get('order_id', 'N/A'),
                        "freed_amount": freed_amount,
                        "score": pos_score,
                        "score_diff": score_diff,
                        "hold_minutes": hold_seconds / 60 if hold_seconds is not None else None
                    })

                    logger.success(
                        f"     ✅ 卖出成功: 订单ID={order_resp.get('order_id', 'N/A')}, "
                        f"释放${freed_amount:,.2f}"
                    )

                    try:
                        await self.stop_manager.update_stop_status(
                            symbol, "rotated_funds"
                        )
                    except Exception:
                        pass

                    if total_freed >= needed_amount:
                        logger.success(
                            f"\n💰 资金释放成功！\n"
                            f"   需要: ${needed_amount:,.2f}\n"
                            f"   已释放: ${total_freed:,.2f}\n"
                            f"   卖出持仓: {', '.join(p['symbol'] for p in sold_positions)}"
                        )
                        return True, total_freed, sold_positions

                except Exception as e:
                    logger.error(f"     ❌ 卖出{symbol}失败: {e}")
                    continue

            if total_freed >= needed_amount:
                logger.success(
                    f"\n💰 资金释放成功！已释放${total_freed:,.2f} (需要${needed_amount:,.2f})"
                )
                return True, total_freed, sold_positions

            logger.warning(
                f"\n⚠️ 资金释放不足：已释放${total_freed:,.2f}，"
                f"还需${needed_amount - total_freed:,.2f}\n"
                f"   可能原因：\n"
                f"   1. 所有持仓评分都高于新信号（保护优质持仓）\n"
                f"   2. 可卖持仓市值不足\n"
                f"   建议：跳过此信号或降低买入数量"
            )
            return False, total_freed, sold_positions

        except Exception as e:
            logger.error(f"❌ 智能轮换执行失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            logger.warning("   建议：检查持仓数据和行情数据是否正常")
            return False, 0.0, []


async def test_rotation():
    """测试持仓轮换功能"""

    settings = get_settings()
    rotator = SmartPositionRotator()

    async with LongportTradingClient(settings) as trade_client, \
               QuoteDataClient(settings) as quote_client:

        # 获取当前持仓
        positions_resp = await trade_client.stock_positions()
        positions = {}

        for channel in positions_resp.channels:
            for pos in channel.positions:
                positions[pos.symbol] = {
                    "quantity": float(pos.quantity) if pos.quantity else 0,  # 转换为float
                    "cost": float(pos.cost_price) if pos.cost_price else 0,
                    "days_held": 0
                }

        logger.info(f"\n当前持仓数: {len(positions)}")

        if len(positions) >= 10:
            logger.info("\n满仓状态，测试持仓评估...")

            # 评估最弱持仓
            weakest = await rotator.find_weakest_positions(
                positions, quote_client, 3
            )

            # 模拟新信号
            test_signal = {
                "symbol": "TEST.HK",
                "strength": 0.9,
                "rsi": 25,
                "macd_golden_cross": True,
                "volume_surge": 2.5,
                "bb_squeeze": True
            }

            logger.info("\n模拟强信号:")
            logger.info(f"  标的: {test_signal['symbol']}")
            logger.info(f"  强度: {test_signal['strength']}")
            logger.info(f"  RSI: {test_signal['rsi']}")

            if weakest:
                await rotator.compare_signal_strength(
                    test_signal, weakest[0][1]
                )
        else:
            logger.info(f"未满仓，还可以开 {10-len(positions)} 个仓位")


async def main():
    logger.info("="*70)
    logger.info("智能持仓轮换系统测试")
    logger.info("="*70)

    await test_rotation()

    logger.info("\n测试完成！")


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════╗
║           智能持仓轮换系统                                    ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  功能特点:                                                    ║
║  🎯 自动评估持仓强度                                          ║
║  📊 多维度评分系统                                            ║
║  🔄 智能持仓轮换                                             ║
║  📈 为强信号腾出空间                                          ║
║                                                              ║
║  评分维度:                                                    ║
║  • 盈亏状况 (30分)                                           ║
║  • 技术指标 (30分)                                           ║
║  • 持仓时间 (20分)                                           ║
║  • 成交量 (10分)                                             ║
║  • 止损距离 (10分)                                           ║
║                                                              ║
║  运行: python3 scripts/smart_position_rotation.py            ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
    """)

    asyncio.run(main())
