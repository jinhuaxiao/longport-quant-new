#!/usr/bin/env python3
"""智能平仓决策交易系统 - 解决止损止盈与技术指标冲突"""

import asyncio
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from loguru import logger
import numpy as np
from typing import Dict, Optional

from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.execution.client import LongportTradingClient
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.features.technical_indicators import TechnicalIndicators


class SmartExitTrader:
    """
    智能平仓决策交易系统

    特点:
    1. 综合评分机制 - 不再硬编码优先级
    2. 止损止盈 + 技术指标 + 持仓时间综合考虑
    3. 分级决策 - 立即卖出/盈利时卖出/减仓/持有
    4. 移动止损 - 锁定利润
    5. 详细的决策日志
    """

    def __init__(self):
        """初始化"""
        self.settings = get_settings()
        self.beijing_tz = ZoneInfo('Asia/Shanghai')

        # 交易参数
        self.budget_per_stock = 5000
        self.max_positions = 5
        self.executed_today = set()

        # 技术指标参数
        self.rsi_period = 14
        self.rsi_oversold = 30
        self.rsi_overbought = 70
        self.bb_period = 20
        self.bb_std = 2.0
        self.macd_fast = 12
        self.macd_slow = 26
        self.macd_signal = 9
        self.atr_period = 14
        self.volume_period = 20
        self.volume_surge_threshold = 1.5

        # 止损止盈参数
        self.atr_stop_multiplier = 2.0
        self.atr_profit_multiplier = 3.0

        # 智能决策参数 ⭐
        self.use_smart_decision = True  # 启用智能决策
        self.use_trailing_stop = True  # 启用移动止损

        # 决策阈值
        self.sell_immediately_threshold = 80  # 立即卖出
        self.sell_profitable_threshold = 60  # 盈利时卖出
        self.reduce_position_threshold = 40  # 减仓50%

        # 移动止损参数
        self.trailing_stop_trigger_pct = 5  # 盈利5%后启动
        self.trailing_stop_distance_pct = 3  # 保持3%距离

        # 持仓管理
        self.positions_with_stops = {}

        logger.info("初始化智能平仓决策交易系统")
        logger.info("特点: 综合评分 + 移动止损 + 分级决策")

    async def run(self):
        """主运行循环"""
        logger.info("=" * 70)
        logger.info("启动智能平仓决策交易系统")
        logger.info("=" * 70)

        async with QuoteDataClient(self.settings) as quote_client, \
                   LongportTradingClient(self.settings) as trade_client:

            self.quote_client = quote_client
            self.trade_client = trade_client

            watchlist = WatchlistLoader().load()
            symbols = list(watchlist.symbols())
            logger.info(f"✅ 监控 {len(symbols)} 个标的")

            account = await self.check_account_status()
            self._display_account_info(account)

            iteration = 0
            while True:
                iteration += 1
                logger.info(f"\n{'='*70}")
                logger.info(f"第 {iteration} 轮扫描 - {datetime.now(self.beijing_tz).strftime('%H:%M:%S')}")
                logger.info(f"{'='*70}")

                try:
                    if not self.is_trading_time():
                        logger.info("⏰ 不在交易时段")
                        await asyncio.sleep(60)
                        continue

                    quotes = await self.get_realtime_quotes(symbols)
                    if not quotes:
                        logger.warning("⚠️  获取行情失败")
                        await asyncio.sleep(60)
                        continue

                    logger.info(f"📊 获取到 {len(quotes)} 个标的的实时行情")

                    account = await self.check_account_status()

                    # 智能平仓检查 ⭐
                    await self.smart_exit_check(quotes, account)

                    # 开仓信号检查（简化版，重点在平仓）
                    for quote in quotes:
                        symbol = quote.symbol
                        current_price = float(quote.last_done)

                        if current_price <= 0:
                            continue

                        if not self._can_open_position(symbol, account):
                            continue

                        # 简单的开仓逻辑
                        signal = await self.generate_entry_signal(symbol, current_price, quote)
                        if signal:
                            await self.execute_entry(symbol, signal, current_price, account)

                    logger.info("\n💤 本轮扫描完成")

                except Exception as e:
                    logger.error(f"❌ 交易循环出错: {e}")
                    import traceback
                    traceback.print_exc()

                logger.info("\n⏳ 等待60秒进入下一轮...")
                await asyncio.sleep(60)

    async def smart_exit_check(self, quotes, account):
        """
        智能平仓检查 ⭐

        核心逻辑:
        1. 计算综合评分 (止损止盈 + 技术指标 + 时间)
        2. 根据评分决定平仓策略
        3. 更新移动止损
        """
        for quote in quotes:
            symbol = quote.symbol
            current_price = float(quote.last_done)

            if current_price <= 0:
                continue

            # 只检查持仓
            if symbol not in account["positions"]:
                continue

            position = account["positions"][symbol]
            entry_price = position["cost"]

            # 确保有止损止盈设置
            if symbol not in self.positions_with_stops:
                await self._set_stops_for_position(symbol, entry_price)
                continue

            stops = self.positions_with_stops[symbol]

            # 更新移动止损
            if self.use_trailing_stop:
                await self._update_trailing_stop(symbol, current_price, stops)

            # 智能决策
            if self.use_smart_decision:
                decision = await self._make_smart_decision(
                    symbol, current_price, position, stops
                )

                if decision["action"] != "HOLD":
                    await self._execute_smart_exit(
                        symbol, current_price, position, decision
                    )
            else:
                # 传统决策（兼容模式）
                await self._traditional_exit_check(
                    symbol, current_price, position, stops
                )

    async def _make_smart_decision(self, symbol, current_price, position, stops):
        """
        智能决策核心算法 ⭐

        返回:
        {
            'action': 'SELL_IMMEDIATELY' | 'SELL_PROFITABLE' | 'REDUCE' | 'HOLD',
            'score': 总评分,
            'reason': 决策原因,
            'scores': {止损评分, 技术评分, 时间评分}
        }
        """
        entry_price = position["cost"]
        stop_loss = stops["stop_loss"]
        take_profit = stops["take_profit"]
        entry_time = stops.get("entry_time", datetime.now(self.beijing_tz))

        pnl_pct = (current_price / entry_price - 1) * 100

        # 计算各维度评分
        stop_score = await self._calculate_stop_score(
            current_price, entry_price, stop_loss, take_profit, pnl_pct
        )

        tech_score = await self._calculate_technical_score(
            symbol, current_price
        )

        time_score = self._calculate_time_score(entry_time)

        # 综合评分
        total_score = stop_score + tech_score + time_score

        # 决策逻辑
        if total_score >= self.sell_immediately_threshold:
            action = "SELL_IMMEDIATELY"
            reason = "综合评分达到立即卖出阈值"

        elif total_score >= self.sell_profitable_threshold:
            if pnl_pct > 0:
                action = "SELL_IMMEDIATELY"
                reason = "综合评分较高且有盈利"
            else:
                action = "REDUCE"
                reason = "综合评分较高但未盈利，减仓50%"

        elif total_score >= self.reduce_position_threshold:
            action = "REDUCE"
            reason = "综合评分中等，减仓50%"

        else:
            action = "HOLD"
            reason = "综合评分较低，继续持有"

        # 详细日志
        logger.info(f"""
╔══════════════════════════════════════════════════════════╗
║ {symbol} 平仓决策分析
╠══════════════════════════════════════════════════════════╣
║ 当前状态:
║   价格: ${current_price:.2f} ({pnl_pct:+.2f}%)
║   入场: ${entry_price:.2f}
║   止损: ${stop_loss:.2f} ({(stop_loss/entry_price-1)*100:+.1f}%)
║   止盈: ${take_profit:.2f} ({(take_profit/entry_price-1)*100:+.1f}%)
║
║ 评分明细:
║   止损止盈: {stop_score:.0f}分
║   技术指标: {tech_score:.0f}分
║   持仓时间: {time_score:.0f}分
║   ────────────────
║   总分: {total_score:.0f}分
║
║ 决策结果: {action}
║ 决策原因: {reason}
╚══════════════════════════════════════════════════════════╝
        """)

        return {
            'action': action,
            'score': total_score,
            'reason': reason,
            'scores': {
                'stop': stop_score,
                'technical': tech_score,
                'time': time_score
            }
        }

    async def _calculate_stop_score(self, current, entry, stop_loss, take_profit, pnl_pct):
        """
        计算止损止盈评分 (0-50分) ⭐

        逻辑:
        - 触及止损: 50分
        - 接近止损 (5%内): 40-50分
        - 触及止盈: 50分
        - 接近止盈 (5%内): 40-50分
        - 大幅盈利 (>10%): 30-40分
        - 中等盈利 (5-10%): 20-30分
        - 小幅盈利 (0-5%): 10-20分
        - 亏损: 0-10分
        """
        score = 0

        # 止损逻辑
        if current <= stop_loss:
            score = 50  # 触及止损
        elif current <= stop_loss * 1.05:
            # 接近止损
            distance_pct = (current - stop_loss) / (entry - stop_loss) * 100
            score = 40 + (1 - distance_pct / 5) * 10

        # 止盈逻辑
        elif current >= take_profit:
            score = 50  # 触及止盈
        elif current >= take_profit * 0.95:
            # 接近止盈
            distance_pct = (take_profit - current) / (take_profit - entry) * 100
            score = 40 + (1 - distance_pct / 5) * 10

        # 盈利逻辑
        elif pnl_pct >= 10:
            score = 30 + min(pnl_pct - 10, 10)  # 30-40分
        elif pnl_pct >= 5:
            score = 20 + (pnl_pct - 5) * 2  # 20-30分
        elif pnl_pct > 0:
            score = 10 + pnl_pct * 2  # 10-20分
        else:
            # 亏损
            score = max(0, 10 + pnl_pct)  # 0-10分

        return score

    async def _calculate_technical_score(self, symbol, current_price):
        """
        计算技术指标评分 (0-30分) ⭐

        逻辑:
        - RSI极度超买 (>80) + 突破布林带: 30分
        - RSI超买 (>70): 20分
        - MACD死叉: 15分
        - 均线死叉: 10分
        - 技术指标中性: 0分
        - 技术指标强势: -10分 (不建议卖出)
        """
        try:
            candles = await self.quote_client.get_history_candles(
                symbol=symbol,
                period=openapi.Period.Day,
                count=60,
                adjust_type=openapi.AdjustType.NoAdjust
            )

            if not candles or len(candles) < 30:
                return 0

            closes = np.array([float(c.close) for c in candles])
            highs = np.array([float(c.high) for c in candles])
            lows = np.array([float(c.low) for c in candles])

            score = 0

            # RSI
            rsi = TechnicalIndicators.rsi(closes, self.rsi_period)
            current_rsi = rsi[-1]

            if current_rsi > 80:
                score += 30  # 极度超买
            elif current_rsi > 70:
                score += 20  # 超买
            elif current_rsi < 30:
                score -= 10  # 超卖，不建议卖

            # 布林带
            bb = TechnicalIndicators.bollinger_bands(closes, self.bb_period, self.bb_std)
            bb_upper = bb['upper'][-1]

            if current_price > bb_upper * 1.02:
                score += 10  # 突破上轨

            # MACD
            macd = TechnicalIndicators.macd(closes, self.macd_fast, self.macd_slow, self.macd_signal)
            macd_hist = macd['histogram'][-1]
            prev_macd_hist = macd['histogram'][-2] if len(macd['histogram']) > 1 else 0

            if macd_hist < 0 and prev_macd_hist > 0:
                score += 15  # 死叉
            elif macd_hist < 0:
                score += 5  # 空头

            # 均线
            sma_20 = TechnicalIndicators.sma(closes, 20)
            sma_50 = TechnicalIndicators.sma(closes, 50)

            if sma_20[-1] < sma_50[-1]:
                score += 10  # 死叉

            return min(30, max(-10, score))

        except Exception as e:
            logger.debug(f"计算技术指标评分失败: {e}")
            return 0

    def _calculate_time_score(self, entry_time):
        """
        计算持仓时间评分 (0-20分) ⭐

        逻辑:
        - 持仓 > 30天: 20分
        - 持仓 > 20天: 15分
        - 持仓 > 10天: 10分
        - 持仓 > 5天: 5分
        - 持仓 < 5天: 0分
        """
        days = (datetime.now(self.beijing_tz) - entry_time).days

        if days > 30:
            return 20
        elif days > 20:
            return 15
        elif days > 10:
            return 10
        elif days > 5:
            return 5
        else:
            return 0

    async def _update_trailing_stop(self, symbol, current_price, stops):
        """
        更新移动止损 ⭐

        规则:
        - 盈利 > 触发百分比: 启动移动止损
        - 止损位 = 当前价 - 距离百分比
        - 只向上移动，不向下
        """
        entry_price = stops["entry_price"]
        current_stop = stops["stop_loss"]

        pnl_pct = (current_price / entry_price - 1) * 100

        # 检查是否触发移动止损
        if pnl_pct < self.trailing_stop_trigger_pct:
            return

        # 计算新止损位
        new_stop = current_price * (1 - self.trailing_stop_distance_pct / 100)

        # 只向上移动
        if new_stop > current_stop:
            old_stop = current_stop
            stops["stop_loss"] = new_stop

            locked_profit_pct = (new_stop / entry_price - 1) * 100

            logger.info(
                f"  📍 {symbol} 移动止损: "
                f"${old_stop:.2f} → ${new_stop:.2f} "
                f"(锁定利润 {locked_profit_pct:+.1f}%)"
            )

    async def _execute_smart_exit(self, symbol, current_price, position, decision):
        """执行智能平仓"""
        quantity = position["quantity"]
        entry_price = position["cost"]
        action = decision["action"]

        try:
            if action == "SELL_IMMEDIATELY":
                # 全部卖出
                await self._execute_sell(symbol, current_price, quantity, decision["reason"])

                # 移除止损记录
                if symbol in self.positions_with_stops:
                    del self.positions_with_stops[symbol]

            elif action == "REDUCE":
                # 减仓50%
                reduce_qty = int(quantity * 0.5)
                if reduce_qty > 0:
                    await self._execute_sell(
                        symbol, current_price, reduce_qty,
                        f"{decision['reason']} (减仓50%)"
                    )

                    # 更新持仓数量（注意：这里简化处理，实际应该重新查询持仓）
                    logger.info(f"  📉 {symbol} 剩余持仓: {quantity - reduce_qty}股")

        except Exception as e:
            logger.error(f"  ❌ {symbol} 智能平仓执行失败: {e}")

    async def _traditional_exit_check(self, symbol, current_price, position, stops):
        """传统平仓检查（兼容模式）"""
        entry_price = position["cost"]
        stop_loss = stops["stop_loss"]
        take_profit = stops["take_profit"]
        pnl_pct = (current_price / entry_price - 1) * 100

        # 止损
        if current_price <= stop_loss:
            logger.warning(f"\n🛑 {symbol} 触及止损!")
            await self._execute_sell(symbol, current_price, position["quantity"], "止损")
            if symbol in self.positions_with_stops:
                del self.positions_with_stops[symbol]
            return

        # 止盈
        if current_price >= take_profit:
            logger.success(f"\n🎉 {symbol} 触及止盈!")
            await self._execute_sell(symbol, current_price, position["quantity"], "止盈")
            if symbol in self.positions_with_stops:
                del self.positions_with_stops[symbol]
            return

    async def _execute_sell(self, symbol, current_price, quantity, reason):
        """执行卖出"""
        try:
            order = await self.trade_client.submit_order({
                "symbol": symbol,
                "side": "SELL",
                "quantity": quantity,
                "price": current_price
            })

            logger.success(
                f"\n✅ 平仓订单已提交: {order['order_id']}\n"
                f"   标的: {symbol}\n"
                f"   原因: {reason}\n"
                f"   数量: {quantity}股\n"
                f"   价格: ${current_price:.2f}"
            )

        except Exception as e:
            logger.error(f"  ❌ {symbol} 平仓失败: {e}")

    # ... 其他辅助方法（与advanced_technical_trading.py相同）
    # is_trading_time, get_realtime_quotes, check_account_status等

    async def is_trading_time(self):
        """检查交易时段（简化）"""
        return True  # 简化实现

    async def get_realtime_quotes(self, symbols):
        """获取实时行情（简化）"""
        try:
            quotes = await self.quote_client.get_realtime_quote(symbols)
            return [q for q in quotes if float(q.last_done) > 0]
        except:
            return []

    async def check_account_status(self):
        """检查账户状态（简化）"""
        return {"cash": {"HKD": 100000}, "positions": {}, "position_count": 0}

    def _display_account_info(self, account):
        """显示账户信息"""
        pass

    def _can_open_position(self, symbol, account):
        """检查是否可以开仓"""
        return False  # 简化：本示例重点在平仓

    async def generate_entry_signal(self, symbol, price, quote):
        """生成入场信号（简化）"""
        return None

    async def execute_entry(self, symbol, signal, price, account):
        """执行入场（简化）"""
        pass

    async def _set_stops_for_position(self, symbol, entry_price):
        """设置止损止盈（简化）"""
        self.positions_with_stops[symbol] = {
            "entry_price": entry_price,
            "stop_loss": entry_price * 0.94,
            "take_profit": entry_price * 1.15,
            "atr": entry_price * 0.03,
            "entry_time": datetime.now(self.beijing_tz)
        }


async def main():
    """主函数"""
    logger.info("\n智能平仓决策交易系统")
    logger.info("特点: 止损止盈与技术指标智能平衡")

    trader = SmartExitTrader()

    try:
        await trader.run()
    except KeyboardInterrupt:
        logger.info("\n收到中断信号，停止系统")


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════╗
║          智能平仓决策交易系统                                  ║
╠═══════════════════════════════════════════════════════════════╣
║                                                               ║
║  核心特性:                                                     ║
║  🎯 综合评分机制 - 不再硬编码优先级                            ║
║  📊 多维度分析 - 止损+技术+时间                                ║
║  🎚️  分级决策 - 立即/盈利/减仓/持有                            ║
║  📈 移动止损 - 自动锁定利润                                    ║
║  📝 详细日志 - 完整决策过程                                    ║
║                                                               ║
║  启动命令: python3 scripts/smart_exit_trading.py             ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
    """)

    asyncio.run(main())