#!/usr/bin/env python3
"""基于技术指标的实时自动交易示例 - RSI + 布林带组合策略"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from loguru import logger
import numpy as np

from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.execution.client import LongportTradingClient
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.features.technical_indicators import TechnicalIndicators
from longport_quant.utils import LotSizeHelper


class TechnicalIndicatorTrader:
    """基于技术指标的实时自动交易系统"""

    def __init__(self):
        """初始化交易系统"""
        self.settings = get_settings()
        self.beijing_tz = ZoneInfo('Asia/Shanghai')

        # 交易参数
        self.budget_per_stock = 5000  # 每只股票预算
        self.max_positions = 5  # 最大持仓数
        self.executed_today = set()  # 今日已交易标的

        # 策略参数
        self.rsi_period = 14
        self.rsi_oversold = 30
        self.rsi_overbought = 70
        self.bb_period = 20
        self.bb_std = 2.0
        self.min_history_days = 50  # 最少需要50天历史数据

        # 手数辅助工具
        self.lot_size_helper = LotSizeHelper()

        logger.info("初始化技术指标交易系统（RSI + 布林带组合策略）")

    async def run(self):
        """主运行循环"""
        logger.info("=" * 60)
        logger.info("启动技术指标自动交易系统")
        logger.info(f"策略：RSI({self.rsi_period}) + 布林带({self.bb_period}, {self.bb_std}σ)")
        logger.info("=" * 60)

        # 初始化客户端
        async with QuoteDataClient(self.settings) as quote_client, \
                   LongportTradingClient(self.settings) as trade_client:

            self.quote_client = quote_client
            self.trade_client = trade_client

            # 加载自选股
            watchlist = WatchlistLoader().load()
            symbols = list(watchlist.symbols())
            logger.info(f"✅ 监控 {len(symbols)} 个标的: {symbols}")

            # 检查账户状态
            account = await self.check_account_status()
            self._display_account_info(account)

            # 主循环
            iteration = 0
            while True:
                iteration += 1
                logger.info(f"\n{'='*60}")
                logger.info(f"第 {iteration} 轮扫描 - {datetime.now(self.beijing_tz).strftime('%H:%M:%S')}")
                logger.info(f"{'='*60}")

                try:
                    # 1. 检查是否在交易时段
                    if not self.is_trading_time():
                        logger.info("⏰ 当前时间: 不在交易时段")
                        await asyncio.sleep(60)
                        continue

                    # 2. 获取实时行情
                    quotes = await self.get_realtime_quotes(symbols)
                    if not quotes:
                        logger.warning("⚠️  获取行情失败或无行情数据")
                        await asyncio.sleep(60)
                        continue

                    logger.info(f"📊 获取到 {len(quotes)} 个标的的实时行情")

                    # 3. 检查持仓和资金
                    account = await self.check_account_status()

                    # 4. 对每个标的进行技术分析
                    for quote in quotes:
                        symbol = quote.symbol
                        current_price = float(quote.last_done)

                        if current_price <= 0:
                            continue

                        # 检查是否可以交易
                        if not self._can_trade(symbol, account):
                            continue

                        # 获取历史数据并计算技术指标
                        try:
                            signal = await self.analyze_symbol(symbol, current_price)

                            if signal:
                                logger.info(f"\n🎯 {symbol} 生成交易信号:")
                                logger.info(f"   类型: {signal['type']}")
                                logger.info(f"   强度: {signal['strength']:.1f}%")
                                logger.info(f"   价格: ${current_price:.2f}")
                                logger.info(f"   RSI: {signal['rsi']:.1f}")
                                logger.info(f"   布林带位置: {signal['bb_position']}")
                                logger.info(f"   原因: {signal['reason']}")

                                # 执行交易
                                await self.execute_signal(symbol, signal, current_price, account)

                        except Exception as e:
                            logger.debug(f"分析 {symbol} 时出错: {e}")

                    logger.info("\n💤 本轮扫描完成")

                except Exception as e:
                    logger.error(f"❌ 交易循环出错: {e}")
                    import traceback
                    traceback.print_exc()

                # 等待下一轮（1分钟）
                logger.info("\n⏳ 等待60秒进入下一轮...")
                await asyncio.sleep(60)

    def is_trading_time(self):
        """检查是否在交易时段"""
        now = datetime.now(self.beijing_tz)
        current_time = now.time()

        # 周末不交易
        if now.weekday() >= 5:
            return False

        # 港股交易时段：9:30-12:00, 13:00-16:00
        from datetime import time
        hk_morning = time(9, 30) <= current_time <= time(12, 0)
        hk_afternoon = time(13, 0) <= current_time <= time(16, 0)

        # 美股交易时段（北京时间）：21:30-次日4:00
        us_trading = current_time >= time(21, 30) or current_time <= time(4, 0)

        return hk_morning or hk_afternoon or us_trading

    async def get_realtime_quotes(self, symbols):
        """获取实时行情"""
        try:
            quotes = await self.quote_client.get_realtime_quote(symbols)
            # 过滤掉价格为0的行情
            return [q for q in quotes if float(q.last_done) > 0]
        except Exception as e:
            logger.error(f"获取行情失败: {e}")
            return []

    async def check_account_status(self):
        """检查账户状态"""
        try:
            balances = await self.trade_client.account_balance()
            positions_resp = await self.trade_client.stock_positions()

            cash = {}
            for balance in balances:
                cash[balance.currency] = float(balance.total_cash)

            positions = {}
            for channel in positions_resp.channels:
                for pos in channel.positions:
                    positions[pos.symbol] = {
                        "quantity": pos.quantity,
                        "available_quantity": pos.available_quantity,
                        "cost": float(pos.cost_price) if pos.cost_price else 0,
                        "currency": pos.currency,
                        "market": pos.market
                    }

            return {
                "cash": cash,
                "positions": positions,
                "position_count": len(positions)
            }

        except Exception as e:
            logger.error(f"查询账户状态失败: {e}")
            return {
                "cash": {"HKD": 0, "USD": 0},
                "positions": {},
                "position_count": 0
            }

    def _display_account_info(self, account):
        """显示账户信息"""
        logger.info("\n📈 账户状态:")
        for currency, amount in account["cash"].items():
            logger.info(f"  💰 {currency} 余额: ${amount:,.2f}")

        logger.info(f"  📦 持仓数: {account['position_count']}/{self.max_positions}")
        if account["positions"]:
            for symbol, pos in account["positions"].items():
                logger.info(f"    - {symbol}: {pos['quantity']}股 @ ${pos['cost']:.2f}")

    def _can_trade(self, symbol, account):
        """检查是否可以交易"""
        # 今日已交易
        if symbol in self.executed_today:
            logger.debug(f"  ⏭️  {symbol}: 今日已交易")
            return False

        # 已达最大持仓数
        if account["position_count"] >= self.max_positions:
            logger.debug(f"  ⏭️  {symbol}: 已达最大持仓数({self.max_positions})")
            return False

        # 已持有该标的
        if symbol in account["positions"]:
            logger.debug(f"  ⏭️  {symbol}: 已持有")
            return False

        return True


    async def analyze_symbol(self, symbol, current_price):
        """
        分析标的并生成交易信号

        策略逻辑：
        1. 强买入: RSI < 30 且价格触及或突破布林带下轨
        2. 买入: RSI < 40 且价格接近布林带下轨（在下轨上方5%以内）
        3. 卖出: RSI > 70 或价格突破布林带上轨
        """
        try:
            # 获取历史K线数据
            end_date = datetime.now().date()
            start_date = end_date - timedelta(days=self.min_history_days + 30)

            candles = await self.quote_client.get_history_candles(
                symbol=symbol,
                period=openapi.Period.Day,
                count=self.min_history_days + 10,
                adjust_type=openapi.AdjustType.NoAdjust
            )

            if not candles or len(candles) < self.min_history_days:
                logger.debug(f"  {symbol}: 历史数据不足({len(candles) if candles else 0}天)")
                return None

            # 提取价格数据
            closes = np.array([float(c.close) for c in candles])
            highs = np.array([float(c.high) for c in candles])
            lows = np.array([float(c.low) for c in candles])
            volumes = np.array([c.volume for c in candles])

            # 计算技术指标
            rsi = TechnicalIndicators.rsi(closes, self.rsi_period)
            bb = TechnicalIndicators.bollinger_bands(closes, self.bb_period, self.bb_std)

            current_rsi = rsi[-1]
            bb_upper = bb['upper'][-1]
            bb_middle = bb['middle'][-1]
            bb_lower = bb['lower'][-1]

            # 检查指标是否有效
            if np.isnan(current_rsi) or np.isnan(bb_lower):
                return None

            # 计算布林带宽度百分比
            bb_width_pct = (bb_upper - bb_lower) / bb_middle * 100

            # 计算当前价格在布林带中的位置（0=下轨，50=中轨，100=上轨）
            if bb_upper != bb_lower:
                bb_position_pct = (current_price - bb_lower) / (bb_upper - bb_lower) * 100
            else:
                bb_position_pct = 50

            # 生成交易信号
            signal = None

            # 强买入信号：RSI超卖 + 触及布林带下轨
            if current_rsi < self.rsi_oversold and current_price <= bb_lower * 1.02:
                signal = {
                    'type': 'STRONG_BUY',
                    'strength': 90,
                    'rsi': current_rsi,
                    'bb_upper': bb_upper,
                    'bb_middle': bb_middle,
                    'bb_lower': bb_lower,
                    'bb_position': f'{bb_position_pct:.1f}%',
                    'bb_width': f'{bb_width_pct:.1f}%',
                    'reason': f'RSI超卖({current_rsi:.1f}) + 触及布林带下轨'
                }

            # 买入信号：RSI接近超卖 + 接近布林带下轨
            elif current_rsi < 40 and current_price <= bb_lower * 1.05:
                signal = {
                    'type': 'BUY',
                    'strength': 70,
                    'rsi': current_rsi,
                    'bb_upper': bb_upper,
                    'bb_middle': bb_middle,
                    'bb_lower': bb_lower,
                    'bb_position': f'{bb_position_pct:.1f}%',
                    'bb_width': f'{bb_width_pct:.1f}%',
                    'reason': f'RSI偏低({current_rsi:.1f}) + 接近布林带下轨'
                }

            # 买入信号：RSI中性 + 价格在布林带下半部 + 布林带收窄（可能突破）
            elif 40 <= current_rsi <= 50 and bb_position_pct < 30 and bb_width_pct < 15:
                signal = {
                    'type': 'BUY',
                    'strength': 60,
                    'rsi': current_rsi,
                    'bb_upper': bb_upper,
                    'bb_middle': bb_middle,
                    'bb_lower': bb_lower,
                    'bb_position': f'{bb_position_pct:.1f}%',
                    'bb_width': f'{bb_width_pct:.1f}%',
                    'reason': f'布林带收窄({bb_width_pct:.1f}%) + 价格低位 + RSI中性'
                }

            return signal

        except Exception as e:
            logger.debug(f"分析 {symbol} 失败: {e}")
            return None

    async def execute_signal(self, symbol, signal, current_price, account):
        """执行交易信号"""
        try:
            signal_type = signal['type']

            # 只执行买入信号（这是自动交易系统，只开仓）
            if signal_type not in ['BUY', 'STRONG_BUY']:
                return

            # 获取股票的交易手数
            lot_size = await self.lot_size_helper.get_lot_size(symbol, self.quote_client)

            # 计算购买数量（必须是手数的整数倍）
            quantity = self.lot_size_helper.calculate_order_quantity(
                symbol, self.budget_per_stock, current_price, lot_size
            )

            if quantity <= 0:
                logger.warning(
                    f"  ⚠️  {symbol}: 预算不足以购买1手 "
                    f"(手数: {lot_size}, 需要: ${lot_size * current_price:.2f})"
                )
                return

            # 计算手数用于日志
            num_lots = quantity // lot_size

            required_cash = current_price * quantity

            # 检查资金
            currency = "HKD" if ".HK" in symbol else "USD"
            available_cash = account["cash"].get(currency, 0)

            if required_cash > available_cash:
                logger.warning(
                    f"  ⚠️  {symbol}: 资金不足 "
                    f"(需要 ${required_cash:.2f}, 可用 ${available_cash:.2f})"
                )
                return

            # 实际下单
            order = await self.trade_client.submit_order({
                "symbol": symbol,
                "side": "BUY",
                "quantity": quantity,
                "price": current_price
            })

            logger.success(
                f"\n✅ 订单已提交: {order['order_id']}\n"
                f"   标的: {symbol}\n"
                f"   类型: {signal_type}\n"
                f"   数量: {quantity}股 ({num_lots}手 × {lot_size}股/手)\n"
                f"   价格: ${current_price:.2f}\n"
                f"   总额: ${required_cash:.2f}\n"
                f"   RSI: {signal['rsi']:.1f}\n"
                f"   布林带位置: {signal['bb_position']}"
            )

            # 标记为已交易
            self.executed_today.add(symbol)

        except Exception as e:
            logger.error(f"  ❌ {symbol} 下单失败: {e}")
            import traceback
            traceback.print_exc()


async def main():
    """主函数"""
    logger.info("\n使用模拟盘API，基于技术指标（RSI + 布林带）的自动交易策略")

    trader = TechnicalIndicatorTrader()

    try:
        await trader.run()
    except KeyboardInterrupt:
        logger.info("\n收到中断信号，停止交易系统")


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════╗
║       技术指标自动交易系统 - RSI + 布林带组合策略             ║
╠═══════════════════════════════════════════════════════════════╣
║                                                               ║
║  策略说明：                                                   ║
║  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━   ║
║                                                               ║
║  📊 使用指标：                                                ║
║    • RSI (相对强弱指标) - 默认周期14                         ║
║    • 布林带 - 默认周期20，标准差2σ                           ║
║                                                               ║
║  🎯 买入信号：                                                ║
║    1. 强买入 (90%强度):                                       ║
║       - RSI < 30 (超卖)                                       ║
║       - 价格触及或突破布林带下轨                              ║
║                                                               ║
║    2. 买入 (70%强度):                                         ║
║       - RSI < 40 (接近超卖)                                   ║
║       - 价格接近布林带下轨 (5%范围内)                         ║
║                                                               ║
║    3. 买入 (60%强度):                                         ║
║       - RSI 40-50 (中性)                                      ║
║       - 价格在布林带下半部 (<30%)                             ║
║       - 布林带收窄 (<15%) - 可能突破信号                      ║
║                                                               ║
║  ⚙️  风控参数：                                               ║
║    • 每只股票预算: $5,000                                     ║
║    • 最大持仓数量: 5只                                        ║
║    • 每只股票每天最多交易1次                                  ║
║    • 需要至少50天历史数据                                     ║
║                                                               ║
║  📈 技术指标解释：                                            ║
║    • RSI < 30: 超卖区域，可能反弹                             ║
║    • RSI > 70: 超买区域，可能回调                             ║
║    • 价格 < 布林带下轨: 价格被低估                            ║
║    • 价格 > 布林带上轨: 价格被高估                            ║
║    • 布林带收窄: 波动率降低，可能突破                         ║
║                                                               ║
║  配置文件: configs/watchlist.yml                              ║
║  启动命令: python3 scripts/technical_indicator_trading.py    ║
║  按 Ctrl+C 停止                                               ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
    """)

    asyncio.run(main())