#!/usr/bin/env python3
"""高级技术指标自动交易系统 - RSI + 布林带 + MACD + 成交量 + 动态止损"""

import asyncio
from datetime import datetime, timedelta, time
from decimal import Decimal
from zoneinfo import ZoneInfo
from loguru import logger
import numpy as np
from typing import Dict, List, Optional

from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.execution.client import LongportTradingClient
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.features.technical_indicators import TechnicalIndicators
from longport_quant.notifications.slack import SlackNotifier


class AdvancedTechnicalTrader:
    """高级技术指标交易系统"""

    def __init__(self, use_builtin_watchlist=False):
        """初始化交易系统

        Args:
            use_builtin_watchlist: 是否使用内置的监控列表（而不是从watchlist.yml加载）
        """
        self.settings = get_settings()
        self.beijing_tz = ZoneInfo('Asia/Shanghai')
        self.slack = None  # Will be initialized in run()
        self.use_builtin_watchlist = use_builtin_watchlist

        # 港股监控列表（移除了有数据问题的ETF和部分标的）
        self.hk_watchlist = {
            # 科技股 (8个)
            "9988.HK": {"name": "阿里巴巴", "sector": "科技"},
            "3690.HK": {"name": "美团", "sector": "科技"},
            "0700.HK": {"name": "腾讯", "sector": "科技"},
            "1810.HK": {"name": "小米", "sector": "科技"},
            "9618.HK": {"name": "京东", "sector": "科技"},
            "1024.HK": {"name": "快手", "sector": "科技"},
            "0981.HK": {"name": "中芯国际", "sector": "科技"},
            "9660.HK": {"name": "地平线机器人", "sector": "科技"},

            # 金融股 (7个)
            "0005.HK": {"name": "汇丰控股", "sector": "金融"},
            "0388.HK": {"name": "港交所", "sector": "金融"},
            "0939.HK": {"name": "建设银行", "sector": "金融"},
            "1398.HK": {"name": "工商银行", "sector": "金融"},
            "3988.HK": {"name": "中国银行", "sector": "金融"},
            "2318.HK": {"name": "中国平安", "sector": "金融"},
            # "3968.HK": {"name": "招商银行", "sector": "金融"},  # 有数据问题，暂时移除

            # 能源股 (3个)
            "0883.HK": {"name": "中海油", "sector": "能源"},
            "0386.HK": {"name": "中国石化", "sector": "能源"},
            "0857.HK": {"name": "中国石油", "sector": "能源"},

            # 消费股 (3个)
            "9992.HK": {"name": "泡泡玛特", "sector": "消费"},
            "1929.HK": {"name": "周大福", "sector": "消费"},
            # "2319.HK": {"name": "蒙牛乳业", "sector": "消费"},  # 已持有
            # "2020.HK": {"name": "安踏体育", "sector": "消费"},  # 有数据问题

            # 汽车股 (2个)
            # "1211.HK": {"name": "比亚迪", "sector": "汽车"},  # 有数据问题
            # "0175.HK": {"name": "吉利汽车", "sector": "汽车"},  # 有数据问题

            # 工业股 (1个)
            "0558.HK": {"name": "力劲科技", "sector": "工业"},
            # "0669.HK": {"name": "创科实业", "sector": "工业"},  # 有数据问题

            # 综合 (1个)
            "0001.HK": {"name": "长和", "sector": "综合"},

            # 地产股 (2个)
            # "1109.HK": {"name": "华润置地", "sector": "地产"},  # 有数据问题
            "0688.HK": {"name": "中国海外发展", "sector": "地产"},

            # 公用事业 (1个)
            "0836.HK": {"name": "华润电力", "sector": "公用事业"},
            # "2688.HK": {"name": "新奥能源", "sector": "公用事业"},  # 有数据问题

            # 博彩股 (1个)
            "1928.HK": {"name": "金沙中国", "sector": "博彩"},
            # "0027.HK": {"name": "银河娱乐", "sector": "博彩"},  # 有数据问题

            # ETF已全部移除（有API限制和数据问题）
        }

        # 美股监控列表
        self.us_watchlist = {
            # 科技大盘股
            "AAPL.US": {"name": "苹果", "sector": "科技"},
            "MSFT.US": {"name": "微软", "sector": "科技"},
            "GOOGL.US": {"name": "谷歌", "sector": "科技"},
            "AMZN.US": {"name": "亚马逊", "sector": "科技"},
            "NVDA.US": {"name": "英伟达", "sector": "科技"},
            "TSLA.US": {"name": "特斯拉", "sector": "汽车"},
            "META.US": {"name": "Meta", "sector": "科技"},
            "AMD.US": {"name": "AMD", "sector": "科技"},

            # 杠杆ETF和新增标的
            "TQQQ.US": {"name": "纳指三倍做多ETF", "sector": "ETF"},
            "NVDU.US": {"name": "英伟达二倍做多ETF", "sector": "ETF"},
            "RKLB.US": {"name": "火箭实验室", "sector": "航天"},
            "HOOD.US": {"name": "Robinhood", "sector": "金融科技"},
        }

        # A股监控列表（如果券商支持）
        self.a_watchlist = {
            "300750.SZ": {"name": "宁德时代", "sector": "新能源"},
        }

        # 交易参数
        self.budget_per_stock = 5000  # 每只股票预算
        self.max_positions = 10  # 最大持仓数（从5增加到10以捕获更多交易机会）
        self.executed_today = set()  # 今日已交易标的

        # 策略参数
        self.rsi_period = 14
        self.rsi_oversold = 30
        self.rsi_overbought = 70
        self.bb_period = 20
        self.bb_std = 2.0
        self.macd_fast = 12
        self.macd_slow = 26
        self.macd_signal = 9
        self.min_history_days = 60  # 需要更多数据来计算MACD

        # 成交量参数
        self.volume_surge_threshold = 1.5  # 成交量放大1.5倍才确认
        self.volume_period = 20  # 成交量均线周期

        # 动态止损参数
        self.atr_period = 14
        self.atr_stop_multiplier = 2.0  # 止损 = ATR × 2
        self.atr_profit_multiplier = 3.0  # 止盈 = ATR × 3
        self.use_dynamic_stops = True  # 使用动态止损

        # 多时间周期参数
        self.use_multi_timeframe = True  # 启用多周期确认
        self.daily_trend_period = 50  # 日线趋势周期

        # 持仓管理
        self.positions_with_stops = {}  # {symbol: {entry_price, stop_loss, take_profit}}

        logger.info("初始化高级技术指标交易系统")
        logger.info(f"策略: RSI + 布林带 + MACD + 成交量确认 + ATR动态止损")

    async def run(self):
        """主运行循环"""
        logger.info("=" * 70)
        logger.info("启动高级技术指标自动交易系统")
        logger.info(f"策略组合: RSI({self.rsi_period}) + BB({self.bb_period},{self.bb_std}σ) + MACD + Volume + ATR")
        logger.info("=" * 70)

        # 初始化客户端
        async with QuoteDataClient(self.settings) as quote_client, \
                   LongportTradingClient(self.settings) as trade_client, \
                   SlackNotifier(self.settings.slack_webhook_url) as slack:

            self.quote_client = quote_client
            self.trade_client = trade_client
            self.slack = slack

            # 加载监控列表
            if self.use_builtin_watchlist:
                # 使用内置监控列表
                symbols = list(self.hk_watchlist.keys()) + list(self.us_watchlist.keys())
                logger.info(f"✅ 使用内置监控列表")
                logger.info(f"   港股: {len(self.hk_watchlist)} 个标的")
                logger.info(f"   美股: {len(self.us_watchlist)} 个标的")
                logger.info(f"   总计: {len(symbols)} 个标的")
            else:
                # 从watchlist.yml加载
                watchlist = WatchlistLoader().load()
                symbols = list(watchlist.symbols())
                logger.info(f"✅ 从配置文件加载监控列表: {len(symbols)} 个标的")

            # 检查账户状态
            account = await self.check_account_status()
            self._display_account_info(account)

            # 主循环
            iteration = 0
            while True:
                iteration += 1
                logger.info(f"\n{'='*70}")
                logger.info(f"第 {iteration} 轮扫描 - {datetime.now(self.beijing_tz).strftime('%H:%M:%S')}")
                logger.info(f"{'='*70}")

                try:
                    # 1. 检查当前活跃市场
                    active_markets, us_session = self.get_active_markets()
                    if not active_markets:
                        logger.info("⏰ 当前时间: 不在交易时段")
                        await asyncio.sleep(60)
                        continue

                    # 2. 根据活跃市场过滤标的
                    active_symbols = self.filter_symbols_by_market(symbols, active_markets)
                    if not active_symbols:
                        logger.info(f"⏰ 当前活跃市场 {active_markets}，但监控列表中无对应标的")
                        await asyncio.sleep(60)
                        continue

                    # 显示活跃市场和交易时段
                    market_info = ', '.join(active_markets)
                    if us_session and 'US' in active_markets:
                        session_label = {'premarket': '盘前', 'regular': '正常', 'afterhours': '盘后'}[us_session]
                        market_info = market_info.replace('US', f'US({session_label})')
                    logger.info(f"📍 活跃市场: {market_info} | 监控标的: {len(active_symbols)}个")

                    # 3. 获取实时行情（只获取活跃市场的标的）
                    quotes = await self.get_realtime_quotes(active_symbols)
                    if not quotes:
                        logger.warning("⚠️  获取行情失败或无行情数据")
                        await asyncio.sleep(60)
                        continue

                    logger.info(f"📊 获取到 {len(quotes)} 个标的的实时行情")

                    # 保存最新行情供智能仓位管理使用
                    self._last_quotes = quotes

                    # 3. 检查持仓和资金
                    account = await self.check_account_status()

                    # 4. 检查现有持仓的止损止盈
                    await self.check_exit_signals(quotes, account)

                    # 5. 对每个标的进行技术分析（开仓信号）
                    for quote in quotes:
                        symbol = quote.symbol
                        current_price = float(quote.last_done)

                        if current_price <= 0:
                            continue

                        # 检查是否可以开仓
                        can_open = self._can_open_position(symbol, account)

                        # 获取历史数据并进行多维度技术分析
                        try:
                            signal = await self.analyze_symbol_advanced(symbol, current_price, quote)

                            if signal:
                                await self._display_signal(symbol, signal, current_price)

                                # 如果不能开仓（满仓），尝试智能清理腾出空间
                                if not can_open:
                                    logger.debug(f"  💼 {symbol}: 满仓，尝试智能仓位管理")
                                    made_room = await self._try_make_room(signal, account)
                                    if made_room:
                                        # 重新获取账户信息
                                        account = await self.get_account_info()
                                        can_open = True
                                        logger.info(f"  ✅ {symbol}: 已腾出空间，可以开仓")
                                    else:
                                        logger.debug(f"  ⏭️  {symbol}: 无法腾出空间，跳过")

                                if can_open:
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

    def get_active_markets(self, include_extended_hours=True):
        """
        获取当前活跃的市场

        Args:
            include_extended_hours: 是否包含美股盘前盘后交易（默认True）

        Returns:
            tuple: (活跃市场列表, 美股交易时段)
                   例如: (['US'], 'premarket') 或 (['HK', 'US'], 'regular')
        """
        now = datetime.now(self.beijing_tz)
        current_time = now.time()
        weekday = now.weekday()

        active_markets = []
        us_session = None

        # 周末不交易
        if weekday >= 5:
            return active_markets, us_session

        # 港股交易时段：9:30-12:00, 13:00-16:00
        hk_morning = time(9, 30) <= current_time <= time(12, 0)
        hk_afternoon = time(13, 0) <= current_time <= time(16, 0)
        if hk_morning or hk_afternoon:
            active_markets.append('HK')

        # 美股交易时段（北京时间）
        if include_extended_hours:
            # 包含盘前盘后：16:00-次日8:00
            # 盘前: 16:00-21:30 (美东04:00-09:30)
            # 正常: 21:30-04:00 (美东09:30-16:00)
            # 盘后: 04:00-08:00 (美东16:00-20:00)
            us_premarket = time(16, 0) <= current_time <= time(21, 30)  # 盘前
            us_regular = current_time >= time(21, 30) or current_time <= time(4, 0)  # 正常
            us_afterhours = time(4, 0) <= current_time <= time(8, 0)  # 盘后

            if us_premarket or us_regular or us_afterhours:
                # 美股周末调整
                if weekday == 5 and current_time > time(8, 0):
                    pass  # 周六白天（8:00后）
                elif weekday == 6:
                    pass  # 周日全天
                else:
                    active_markets.append('US')
                    # 确定交易时段
                    if us_premarket:
                        us_session = 'premarket'
                    elif us_afterhours:
                        us_session = 'afterhours'
                    else:
                        us_session = 'regular'
        else:
            # 仅正常交易时段：21:30-次日4:00
            if current_time >= time(21, 30) or current_time <= time(4, 0):
                # 美股周末调整
                if weekday == 5 and current_time > time(4, 0):
                    pass  # 周六白天
                elif weekday == 6:
                    pass  # 周日全天
                else:
                    active_markets.append('US')
                    us_session = 'regular'

        return active_markets, us_session

    def is_trading_time(self):
        """检查是否在交易时段"""
        active_markets, us_session = self.get_active_markets()

        if not active_markets:
            now = datetime.now(self.beijing_tz)
            current_time = now.time()
            logger.debug(
                f"  ⏰ 当前时间 {current_time.strftime('%H:%M')} "
                f"不在交易时段 (港股9:30-16:00, 美股16:00-次日8:00含盘前盘后)"
            )
            return False

        return True

    def filter_symbols_by_market(self, symbols, active_markets):
        """
        根据活跃市场过滤标的

        Args:
            symbols: 标的列表
            active_markets: 活跃市场列表 ['HK', 'US']

        Returns:
            list: 过滤后的标的列表
        """
        if not active_markets:
            return []

        filtered = []
        for symbol in symbols:
            if 'HK' in active_markets and '.HK' in symbol:
                filtered.append(symbol)
            elif 'US' in active_markets and '.US' in symbol:
                filtered.append(symbol)

        return filtered

    async def get_realtime_quotes(self, symbols):
        """获取实时行情"""
        try:
            quotes = await self.quote_client.get_realtime_quote(symbols)

            # 过滤有效行情
            valid_quotes = []
            for q in quotes:
                try:
                    price = float(q.last_done)
                    if price > 0:
                        valid_quotes.append(q)
                    else:
                        logger.debug(f"  {q.symbol}: 价格为0，跳过")
                except Exception as e:
                    logger.debug(f"  {q.symbol}: 解析价格失败 - {e}")

            if not valid_quotes:
                logger.info("  📊 所有标的价格为0（可能不在交易时段或盘前盘后）")

            return valid_quotes

        except Exception as e:
            logger.error(f"获取行情失败: {e}")
            import traceback
            traceback.print_exc()
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
                stop_info = ""
                if symbol in self.positions_with_stops:
                    stops = self.positions_with_stops[symbol]
                    stop_info = f" | 止损: ${stops['stop_loss']:.2f} | 止盈: ${stops['take_profit']:.2f}"
                logger.info(f"    - {symbol}: {pos['quantity']}股 @ ${pos['cost']:.2f}{stop_info}")

    def _can_open_position(self, symbol, account):
        """检查是否可以开仓"""
        if symbol in self.executed_today:
            logger.debug(f"  ⏭️  {symbol}: 今日已交易")
            return False

        if symbol in account["positions"]:
            logger.debug(f"  ⏭️  {symbol}: 已持有")
            return False

        # 如果未达到最大持仓数，直接允许
        if account["position_count"] < self.max_positions:
            return True

        # 如果已满仓，返回False（需要通过 _try_make_room 来清理）
        logger.debug(f"  ⏭️  {symbol}: 已达最大持仓数({self.max_positions})")
        return False

    async def _try_make_room(self, new_signal, account):
        """
        智能仓位管理：当满仓时，评估是否应该清理弱势持仓为新信号腾出空间

        清理优先级（从高到低）：
        1. 已触发止损但未执行的持仓
        2. 亏损接近止损位的持仓（评分低）
        3. 盈利但技术指标转弱的持仓
        4. 盈利最少的持仓

        Returns:
            bool: 是否成功腾出空间
        """
        if account["position_count"] < self.max_positions:
            return True  # 有空位，不需要清理

        # 只有强买入信号才考虑清理
        if new_signal['type'] not in ['STRONG_BUY', 'BUY']:
            return False

        # 评估所有持仓的质量
        positions_to_evaluate = []

        for symbol, position in account["positions"].items():
            cost_price = position["cost"]
            quantity = position["quantity"]

            # 获取当前价格（需要从最近的quotes中获取）
            current_price = None
            if hasattr(self, '_last_quotes'):
                for q in self._last_quotes:
                    if q.symbol == symbol:
                        current_price = float(q.last_done)
                        break

            if not current_price or current_price <= 0:
                continue

            pnl_pct = (current_price / cost_price - 1) * 100

            # 计算持仓评分（越低越应该清理）
            score = 50  # 基础分

            # 1. 盈亏评分（-10分到+30分）
            if pnl_pct < -5:
                score -= 20  # 大幅亏损
            elif pnl_pct < -3:
                score -= 10  # 亏损接近止损
            elif pnl_pct < 0:
                score -= 5   # 小幅亏损
            elif pnl_pct > 15:
                score += 30  # 大幅盈利
            elif pnl_pct > 10:
                score += 20  # 盈利良好
            elif pnl_pct > 5:
                score += 10  # 小幅盈利

            # 2. 止损止盈状态评分
            if symbol in self.positions_with_stops:
                stops = self.positions_with_stops[symbol]
                stop_loss = stops["stop_loss"]
                take_profit = stops["take_profit"]

                # 已触发止损
                if current_price <= stop_loss:
                    score = 0  # 最低分，应该立即清理
                    logger.warning(f"  ⚠️  {symbol} 已触发止损但未执行，应清理")

                # 接近止损
                elif current_price < stop_loss * 1.02:
                    score -= 15
                    logger.debug(f"  ⚠️  {symbol} 接近止损位")

                # 已触发止盈
                elif current_price >= take_profit:
                    score += 15  # 盈利持仓，但可以考虑获利了结

            # 3. 持仓时间评分（避免频繁交易）
            # 这里简化处理，实际应该记录持仓时间

            positions_to_evaluate.append({
                'symbol': symbol,
                'score': score,
                'pnl_pct': pnl_pct,
                'current_price': current_price,
                'position': position
            })

        if not positions_to_evaluate:
            return False

        # 按评分排序，找出最弱的持仓
        positions_to_evaluate.sort(key=lambda x: x['score'])
        weakest = positions_to_evaluate[0]

        # 对比新信号和最弱持仓
        new_signal_score = new_signal['strength']

        # 决策逻辑
        should_clear = False
        clear_reason = ""

        if weakest['score'] == 0:
            # 已触发止损
            should_clear = True
            clear_reason = "已触发止损"
        elif weakest['score'] < 30 and new_signal_score > 70:
            # 弱势持仓 + 强新信号
            should_clear = True
            clear_reason = f"弱势持仓(评分:{weakest['score']}) vs 强信号(评分:{new_signal_score})"
        elif weakest['pnl_pct'] < -3 and new_signal_score > 65:
            # 亏损持仓 + 较强新信号
            should_clear = True
            clear_reason = f"亏损持仓({weakest['pnl_pct']:.1f}%) vs 强信号(评分:{new_signal_score})"

        if should_clear:
            logger.info(
                f"\n🔄 智能仓位管理: 清理 {weakest['symbol']} 为新信号腾出空间\n"
                f"   原因: {clear_reason}\n"
                f"   {weakest['symbol']} 评分: {weakest['score']}, 盈亏: {weakest['pnl_pct']:.2f}%\n"
                f"   新信号评分: {new_signal_score}/100"
            )

            # 发送Slack通知
            if self.slack:
                message = (
                    f"🔄 *智能仓位管理*\n\n"
                    f"📊 清理持仓: {weakest['symbol']}\n"
                    f"💯 持仓评分: {weakest['score']}/100\n"
                    f"📈 盈亏: {weakest['pnl_pct']:.2f}%\n"
                    f"💡 原因: {clear_reason}\n\n"
                    f"🆕 新信号: {new_signal['type']}\n"
                    f"⭐ 新信号评分: {new_signal_score}/100\n"
                    f"🎯 为更优质的机会腾出空间"
                )
                await self.slack.send(message)

            # 执行卖出
            await self._execute_sell(
                weakest['symbol'],
                weakest['current_price'],
                weakest['position'],
                f"仓位管理：{clear_reason}"
            )

            return True

        logger.debug(
            f"  💼 保持当前持仓: 最弱持仓评分({weakest['score']}) vs 新信号评分({new_signal_score})"
        )
        return False

    async def analyze_symbol_advanced(self, symbol, current_price, quote):
        """
        高级多维度技术分析

        分析维度:
        1. RSI: 超卖/超买判断
        2. 布林带: 价格位置
        3. MACD: 趋势确认
        4. 成交量: 放量确认
        5. ATR: 波动率和止损位
        6. 多周期趋势: 日线趋势确认
        """
        try:
            # 获取历史K线数据 - 增加天数以获得更完整的MACD数据
            from datetime import timedelta
            end_date = datetime.now()
            # 对ETF使用更少的历史天数
            is_etf = any(etf in symbol for etf in ['2800', '2822', '2828', '3188', '9919', '3110', '2801', '2827', '9067', '2819'])
            # 增加历史数据天数：ETF 60天，普通股票 100天（确保MACD有足够数据）
            days_to_fetch = 60 if is_etf else 100

            start_date = end_date - timedelta(days=days_to_fetch)

            candles = await self.quote_client.get_history_candles(
                symbol=symbol,
                period=openapi.Period.Day,
                adjust_type=openapi.AdjustType.NoAdjust,
                start=start_date,
                end=end_date
            )

            if not candles or len(candles) < 30:  # 降低最小要求
                logger.debug(f"  {symbol}: 历史数据不足({len(candles) if candles else 0}天)")
                return None

            # 提取数据
            closes = np.array([float(c.close) for c in candles])
            highs = np.array([float(c.high) for c in candles])
            lows = np.array([float(c.low) for c in candles])
            volumes = np.array([c.volume for c in candles])

            # 确保数据长度一致
            min_len = min(len(closes), len(highs), len(lows), len(volumes))
            closes = closes[-min_len:]
            highs = highs[-min_len:]
            lows = lows[-min_len:]
            volumes = volumes[-min_len:]

            # 计算所有技术指标
            indicators = self._calculate_all_indicators(closes, highs, lows, volumes)

            # 检查指标有效性
            if not self._validate_indicators(indicators):
                return None

            # 分析买入信号
            signal = self._analyze_buy_signals(
                symbol, current_price, quote, indicators, closes, highs, lows
            )

            return signal

        except Exception as e:
            # 只在非API限制错误时记录详细日志
            if "301607" not in str(e):  # 不记录API限制错误
                logger.debug(f"分析 {symbol} 失败: {e}")
            return None

    def _calculate_all_indicators(self, closes, highs, lows, volumes):
        """计算所有技术指标"""
        try:
            # RSI
            rsi = TechnicalIndicators.rsi(closes, min(self.rsi_period, len(closes) - 1))

            # 布林带
            bb = TechnicalIndicators.bollinger_bands(closes, min(self.bb_period, len(closes) - 1), self.bb_std)

            # MACD
            macd = TechnicalIndicators.macd(
                closes,
                min(self.macd_fast, len(closes) - 1),
                min(self.macd_slow, len(closes) - 1),
                min(self.macd_signal, len(closes) - 1)
            )

            # 成交量
            volume_sma = TechnicalIndicators.sma(volumes, min(self.volume_period, len(volumes) - 1))

            # ATR (Average True Range)
            atr = TechnicalIndicators.atr(highs, lows, closes, min(self.atr_period, len(closes) - 1))

            # 趋势指标 (SMA)
            sma_20 = TechnicalIndicators.sma(closes, min(20, len(closes) - 1))
            sma_50 = TechnicalIndicators.sma(closes, min(50, len(closes) - 1)) if len(closes) >= 50 else sma_20

            return {
                'rsi': rsi[-1] if len(rsi) > 0 else np.nan,
                'bb_upper': bb['upper'][-1] if len(bb['upper']) > 0 else np.nan,
                'bb_middle': bb['middle'][-1] if len(bb['middle']) > 0 else np.nan,
                'bb_lower': bb['lower'][-1] if len(bb['lower']) > 0 else np.nan,
                'macd_line': macd['macd'][-1] if len(macd['macd']) > 0 else np.nan,
                'macd_signal': macd['signal'][-1] if len(macd['signal']) > 0 else np.nan,
                'macd_histogram': macd['histogram'][-1] if len(macd['histogram']) > 0 else np.nan,
                'volume_sma': volume_sma[-1] if len(volume_sma) > 0 else np.nan,
                'atr': atr[-1] if len(atr) > 0 else np.nan,
                'sma_20': sma_20[-1] if len(sma_20) > 0 else np.nan,
                'sma_50': sma_50[-1] if len(sma_50) > 0 else np.nan,
                # 前一期数据用于判断交叉
                'prev_macd_histogram': macd['histogram'][-2] if len(macd['histogram']) > 1 else 0,
            }
        except Exception as e:
            logger.debug(f"计算技术指标失败: {e}")
            return {
                'rsi': np.nan,
                'bb_upper': np.nan,
                'bb_middle': np.nan,
                'bb_lower': np.nan,
                'macd_line': np.nan,
                'macd_signal': np.nan,
                'macd_histogram': np.nan,
                'volume_sma': np.nan,
                'atr': np.nan,
                'sma_20': np.nan,
                'sma_50': np.nan,
                'prev_macd_histogram': 0,
            }

    def _validate_indicators(self, indicators):
        """验证指标有效性"""
        required = ['rsi', 'bb_lower', 'macd_line', 'atr']
        return all(not np.isnan(indicators.get(key, np.nan)) for key in required)

    def _analyze_buy_signals(self, symbol, current_price, quote, ind, closes, highs, lows):
        """
        综合分析买入信号

        信号强度评分系统:
        - RSI超卖: 0-30分
        - 布林带位置: 0-25分
        - MACD金叉: 0-20分
        - 成交量确认: 0-15分
        - 趋势确认: 0-10分
        总分: 0-100分
        """
        score = 0
        reasons = []

        # 计算当前成交量比率
        current_volume = quote.volume
        volume_ratio = current_volume / ind['volume_sma'] if ind['volume_sma'] > 0 else 1

        # 计算布林带位置
        bb_range = ind['bb_upper'] - ind['bb_lower']
        if bb_range > 0:
            bb_position_pct = (current_price - ind['bb_lower']) / bb_range * 100
        else:
            bb_position_pct = 50

        bb_width_pct = bb_range / ind['bb_middle'] * 100 if ind['bb_middle'] > 0 else 0

        # === 1. RSI分析 (0-30分) ===
        rsi_score = 0
        if ind['rsi'] < 20:  # 极度超卖
            rsi_score = 30
            reasons.append(f"RSI极度超卖({ind['rsi']:.1f})")
        elif ind['rsi'] < self.rsi_oversold:  # 超卖
            rsi_score = 25
            reasons.append(f"RSI超卖({ind['rsi']:.1f})")
        elif ind['rsi'] < 40:  # 接近超卖
            rsi_score = 15
            reasons.append(f"RSI偏低({ind['rsi']:.1f})")
        elif 40 <= ind['rsi'] <= 50:  # 中性偏低
            rsi_score = 5
            reasons.append(f"RSI中性({ind['rsi']:.1f})")

        score += rsi_score

        # === 2. 布林带分析 (0-25分) ===
        bb_score = 0
        if current_price <= ind['bb_lower']:  # 触及或突破下轨
            bb_score = 25
            reasons.append(f"触及布林带下轨(${ind['bb_lower']:.2f})")
        elif current_price <= ind['bb_lower'] * 1.02:  # 接近下轨
            bb_score = 20
            reasons.append(f"接近布林带下轨")
        elif bb_position_pct < 30:  # 在下半部
            bb_score = 10
            reasons.append(f"布林带下半部({bb_position_pct:.0f}%)")

        # 布林带收窄加分
        if bb_width_pct < 10:
            bb_score += 5
            reasons.append(f"布林带极度收窄({bb_width_pct:.1f}%)")
        elif bb_width_pct < 15:
            bb_score += 3
            reasons.append(f"布林带收窄")

        score += bb_score

        # === 3. MACD分析 (0-20分) ===
        macd_score = 0
        # MACD金叉: histogram从负转正
        if ind['macd_histogram'] > 0 and ind['prev_macd_histogram'] <= 0:
            macd_score = 20
            reasons.append("MACD金叉(刚上穿)")
        elif ind['macd_histogram'] > 0 and ind['macd_line'] > ind['macd_signal']:
            macd_score = 15
            reasons.append("MACD多头")
        elif ind['macd_histogram'] > ind['prev_macd_histogram'] > 0:
            macd_score = 10
            reasons.append("MACD柱状图扩大")

        score += macd_score

        # === 4. 成交量确认 (0-15分) ===
        volume_score = 0
        if volume_ratio >= 2.0:  # 放量2倍以上
            volume_score = 15
            reasons.append(f"成交量大幅放大({volume_ratio:.1f}x)")
        elif volume_ratio >= self.volume_surge_threshold:  # 放量1.5倍
            volume_score = 10
            reasons.append(f"成交量放大({volume_ratio:.1f}x)")
        elif volume_ratio >= 1.2:  # 温和放量
            volume_score = 5
            reasons.append(f"成交量温和({volume_ratio:.1f}x)")

        score += volume_score

        # === 5. 趋势确认 (0-10分) ===
        trend_score = 0
        if self.use_multi_timeframe:
            # 价格在20日均线上方
            if current_price > ind['sma_20']:
                trend_score += 3
                reasons.append("价格在SMA20上方")

            # 短期均线在长期均线上方(金叉)
            if ind['sma_20'] > ind['sma_50']:
                trend_score += 7
                reasons.append("SMA20在SMA50上方(上升趋势)")
            elif ind['sma_20'] > ind['sma_50'] * 0.98:  # 接近金叉
                trend_score += 4
                reasons.append("接近均线金叉")

        score += trend_score

        # === 生成信号 ===
        if score >= 60:  # 强买入信号
            signal_type = "STRONG_BUY"
        elif score >= 45:  # 买入信号
            signal_type = "BUY"
        elif score >= 30:  # 弱买入信号
            signal_type = "WEAK_BUY"
        else:
            return None  # 分数太低，不生成信号

        # 计算动态止损止盈
        if self.use_dynamic_stops and not np.isnan(ind['atr']):
            stop_loss = current_price - ind['atr'] * self.atr_stop_multiplier
            take_profit = current_price + ind['atr'] * self.atr_profit_multiplier
        else:
            stop_loss = current_price * 0.95  # 默认5%止损
            take_profit = current_price * 1.15  # 默认15%止盈

        return {
            'type': signal_type,
            'strength': score,
            'rsi': ind['rsi'],
            'bb_upper': ind['bb_upper'],
            'bb_middle': ind['bb_middle'],
            'bb_lower': ind['bb_lower'],
            'bb_position': f'{bb_position_pct:.1f}%',
            'bb_width': f'{bb_width_pct:.1f}%',
            'macd_line': ind['macd_line'],
            'macd_signal': ind['macd_signal'],
            'macd_histogram': ind['macd_histogram'],
            'volume_ratio': volume_ratio,
            'atr': ind['atr'],
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'trend': 'bullish' if ind['sma_20'] > ind['sma_50'] else 'bearish',
            'reasons': reasons
        }

    async def _display_signal(self, symbol, signal, current_price):
        """显示交易信号（仅日志，不发送Slack通知）"""
        logger.info(f"\n🎯 {symbol} 生成交易信号:")
        logger.info(f"   类型: {signal['type']}")
        logger.info(f"   综合评分: {signal['strength']:.0f}/100")
        logger.info(f"   当前价格: ${current_price:.2f}")
        logger.info(f"   RSI: {signal['rsi']:.1f}")
        logger.info(f"   布林带位置: {signal['bb_position']} (宽度: {signal['bb_width']})")
        logger.info(f"   MACD: {signal['macd_histogram']:.3f}")
        logger.info(f"   成交量比率: {signal['volume_ratio']:.2f}x")
        logger.info(f"   ATR: ${signal['atr']:.2f}")
        logger.info(f"   趋势: {signal['trend']}")
        logger.info(f"   止损位: ${signal['stop_loss']:.2f} ({(signal['stop_loss']/current_price-1)*100:.1f}%)")
        logger.info(f"   止盈位: ${signal['take_profit']:.2f} ({(signal['take_profit']/current_price-1)*100:.1f}%)")
        logger.info(f"   原因: {', '.join(signal['reasons'])}")

        # 注意：Slack通知只在真正执行交易时发送（在execute_signal中）
        # 避免检测到信号但未执行时重复发送通知

    async def check_exit_signals(self, quotes, account):
        """
        检查现有持仓的平仓信号

        平仓条件:
        1. 触及止损位
        2. 触及止盈位
        3. RSI超买 + 价格突破布林带上轨
        """
        for quote in quotes:
            symbol = quote.symbol
            current_price = float(quote.last_done)

            if current_price <= 0:
                continue

            # 只检查持仓的标的
            if symbol not in account["positions"]:
                continue

            position = account["positions"][symbol]
            entry_price = position["cost"]

            # 检查是否有设置止损止盈
            if symbol not in self.positions_with_stops:
                # 如果没有，尝试根据当前ATR设置
                try:
                    await self._set_stops_for_position(symbol, entry_price)
                except Exception as e:
                    logger.debug(f"  {symbol}: 无法设置止损止盈 - {e}")
                    continue

            # 再次检查是否成功设置
            if symbol not in self.positions_with_stops:
                logger.debug(f"  {symbol}: 跳过止损止盈检查（未设置）")
                continue

            stops = self.positions_with_stops[symbol]
            stop_loss = stops["stop_loss"]
            take_profit = stops["take_profit"]

            # 计算盈亏
            pnl_pct = (current_price / entry_price - 1) * 100

            # 检查止损
            if current_price <= stop_loss:
                logger.warning(
                    f"\n🛑 {symbol} 触及止损位!\n"
                    f"   入场价: ${entry_price:.2f}\n"
                    f"   当前价: ${current_price:.2f}\n"
                    f"   止损位: ${stop_loss:.2f}\n"
                    f"   盈亏: {pnl_pct:.2f}%"
                )

                # 发送Slack通知
                if self.slack:
                    message = (
                        f"🛑 *止损触发*: {symbol}\n\n"
                        f"💵 入场价: ${entry_price:.2f}\n"
                        f"💸 当前价: ${current_price:.2f}\n"
                        f"🎯 止损位: ${stop_loss:.2f}\n"
                        f"📉 盈亏: *{pnl_pct:.2f}%*\n"
                        f"⚠️ 将执行卖出操作"
                    )
                    await self.slack.send(message)

                await self._execute_sell(symbol, current_price, position, "止损")
                continue

            # 检查止盈
            if current_price >= take_profit:
                logger.success(
                    f"\n🎉 {symbol} 触及止盈位!\n"
                    f"   入场价: ${entry_price:.2f}\n"
                    f"   当前价: ${current_price:.2f}\n"
                    f"   止盈位: ${take_profit:.2f}\n"
                    f"   盈亏: {pnl_pct:.2f}%"
                )

                # 智能止盈：重新分析技术指标，判断是否应该继续持有
                should_hold = False
                hold_reason = ""

                try:
                    # 重新分析当前的技术指标
                    current_signal = await self.analyze_symbol_advanced(symbol, current_price)

                    if current_signal and current_signal['type'] in ['STRONG_BUY', 'BUY']:
                        # 如果技术指标仍然是买入信号，考虑继续持有
                        should_hold = True
                        hold_reason = f"技术指标仍显示{current_signal['type']}信号 (评分: {current_signal['strength']:.0f}/100)"

                        logger.info(
                            f"\n💡 {symbol} 智能止盈决策: 继续持有\n"
                            f"   原因: {hold_reason}\n"
                            f"   RSI: {current_signal.get('rsi', 'N/A')}\n"
                            f"   MACD: {current_signal.get('macd', 'N/A')}\n"
                            f"   趋势: {current_signal.get('trend', 'N/A')}"
                        )

                        # 更新止盈位到更高位置（移动止盈）
                        if 'take_profit' in current_signal:
                            new_take_profit = current_signal['take_profit']
                            if new_take_profit > take_profit:
                                self.positions_with_stops[symbol]['take_profit'] = new_take_profit
                                logger.info(f"   📈 移动止盈位: ${take_profit:.2f} → ${new_take_profit:.2f}")

                        # 发送Slack通知
                        if self.slack:
                            indicators_info = ""
                            if 'rsi' in current_signal:
                                indicators_info += f"   • RSI: {current_signal['rsi']:.1f}\n"
                            if 'macd' in current_signal:
                                indicators_info += f"   • MACD: {current_signal['macd']:.3f}\n"
                            if 'trend' in current_signal:
                                indicators_info += f"   • 趋势: {current_signal['trend']}\n"

                            message = (
                                f"💡 *智能止盈 - 继续持有*: {symbol}\n\n"
                                f"💵 入场价: ${entry_price:.2f}\n"
                                f"💰 当前价: ${current_price:.2f}\n"
                                f"🎁 原止盈位: ${take_profit:.2f}\n"
                                f"📈 当前盈亏: *+{pnl_pct:.2f}%*\n\n"
                                f"🔍 *持有理由*:\n{hold_reason}\n\n"
                                f"📊 *当前技术指标*:\n{indicators_info}\n"
                                f"✅ 继续持有，等待更好的退出机会"
                            )
                            await self.slack.send(message)

                except Exception as e:
                    logger.debug(f"  {symbol}: 无法分析当前信号 - {e}")
                    # 如果无法分析，默认执行止盈
                    should_hold = False

                if not should_hold:
                    # 执行止盈卖出
                    if self.slack:
                        message = (
                            f"🎉 *止盈触发 - 执行卖出*: {symbol}\n\n"
                            f"💵 入场价: ${entry_price:.2f}\n"
                            f"💰 当前价: ${current_price:.2f}\n"
                            f"🎁 止盈位: ${take_profit:.2f}\n"
                            f"📈 盈亏: *+{pnl_pct:.2f}%*\n"
                            f"✅ 将执行卖出操作"
                        )
                        await self.slack.send(message)

                    await self._execute_sell(symbol, current_price, position, "止盈")
                    continue
                else:
                    # 继续持有，跳过本次卖出
                    continue

            # 技术指标平仓信号（可选）
            try:
                exit_signal = await self._check_technical_exit(symbol, current_price)
                if exit_signal:
                    logger.info(
                        f"\n⚠️  {symbol} 技术指标平仓信号\n"
                        f"   当前价: ${current_price:.2f}\n"
                        f"   盈亏: {pnl_pct:.2f}%\n"
                        f"   原因: {exit_signal}"
                    )
                    await self._execute_sell(symbol, current_price, position, exit_signal)
            except:
                pass

    async def _set_stops_for_position(self, symbol, entry_price):
        """为持仓设置止损止盈"""
        try:
            from datetime import timedelta
            end_date = datetime.now()
            start_date = end_date - timedelta(days=60)

            candles = await self.quote_client.get_history_candles(
                symbol=symbol,
                period=openapi.Period.Day,
                adjust_type=openapi.AdjustType.NoAdjust,
                start=start_date,
                end=end_date
            )

            if candles and len(candles) >= self.atr_period:
                highs = np.array([float(c.high) for c in candles])
                lows = np.array([float(c.low) for c in candles])
                closes = np.array([float(c.close) for c in candles])

                atr = TechnicalIndicators.atr(highs, lows, closes, self.atr_period)
                current_atr = atr[-1]

                if not np.isnan(current_atr):
                    stop_loss = entry_price - current_atr * self.atr_stop_multiplier
                    take_profit = entry_price + current_atr * self.atr_profit_multiplier

                    self.positions_with_stops[symbol] = {
                        "entry_price": entry_price,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "atr": current_atr
                    }

                    logger.info(
                        f"  📍 {symbol} 设置止损止盈: "
                        f"止损=${stop_loss:.2f} ({(stop_loss/entry_price-1)*100:.1f}%), "
                        f"止盈=${take_profit:.2f} ({(take_profit/entry_price-1)*100:.1f}%)"
                    )
        except Exception as e:
            logger.debug(f"设置止损失败: {e}")

    async def _check_technical_exit(self, symbol, current_price):
        """检查技术指标平仓信号"""
        try:
            from datetime import timedelta
            end_date = datetime.now()
            start_date = end_date - timedelta(days=60)

            candles = await self.quote_client.get_history_candles(
                symbol=symbol,
                period=openapi.Period.Day,
                adjust_type=openapi.AdjustType.NoAdjust,
                start=start_date,
                end=end_date
            )

            if not candles or len(candles) < 20:
                return None

            closes = np.array([float(c.close) for c in candles])
            highs = np.array([float(c.high) for c in candles])
            lows = np.array([float(c.low) for c in candles])

            # RSI
            rsi = TechnicalIndicators.rsi(closes, self.rsi_period)
            current_rsi = rsi[-1]

            # 布林带
            bb = TechnicalIndicators.bollinger_bands(closes, self.bb_period, self.bb_std)
            bb_upper = bb['upper'][-1]

            # RSI超买 + 突破布林带上轨
            if current_rsi > self.rsi_overbought and current_price > bb_upper:
                return f"RSI超买({current_rsi:.1f}) + 突破布林带上轨"

            return None

        except:
            return None

    async def _execute_sell(self, symbol, current_price, position, reason):
        """执行卖出"""
        try:
            quantity = position["quantity"]

            order = await self.trade_client.submit_order({
                "symbol": symbol,
                "side": "SELL",
                "quantity": quantity,
                "price": current_price
            })

            entry_price = position["cost"]
            # Convert Decimal to float for calculation
            pnl = (current_price - entry_price) * float(quantity)
            pnl_pct = (current_price / entry_price - 1) * 100

            logger.success(
                f"\n✅ 平仓订单已提交: {order['order_id']}\n"
                f"   标的: {symbol}\n"
                f"   原因: {reason}\n"
                f"   数量: {quantity}股\n"
                f"   入场价: ${entry_price:.2f}\n"
                f"   平仓价: ${current_price:.2f}\n"
                f"   盈亏: ${pnl:.2f} ({pnl_pct:+.2f}%)"
            )

            # 发送Slack通知
            if self.slack:
                emoji = "✅" if pnl > 0 else "❌"
                message = (
                    f"{emoji} *平仓订单已提交*\n\n"
                    f"📋 订单ID: `{order['order_id']}`\n"
                    f"📊 标的: *{symbol}*\n"
                    f"📝 原因: {reason}\n"
                    f"📦 数量: {quantity}股\n"
                    f"💵 入场价: ${entry_price:.2f}\n"
                    f"💰 平仓价: ${current_price:.2f}\n"
                    f"💹 盈亏: ${pnl:.2f} (*{pnl_pct:+.2f}%*)"
                )
                await self.slack.send(message)

            # 移除止损记录
            if symbol in self.positions_with_stops:
                del self.positions_with_stops[symbol]

        except Exception as e:
            logger.error(f"  ❌ {symbol} 平仓失败: {e}")

    async def execute_signal(self, symbol, signal, current_price, account):
        """执行开仓信号"""
        try:
            signal_type = signal['type']

            # 弱买入信号需要更严格的条件
            if signal_type == "WEAK_BUY" and signal['strength'] < 35:
                logger.debug(f"  跳过弱买入信号 (评分: {signal['strength']})")
                return

            # 计算购买数量
            quantity = int(self.budget_per_stock / current_price)
            if quantity <= 0:
                logger.warning(f"  ⚠️  {symbol}: 预算不足以购买1股")
                return

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

            # 下单
            order = await self.trade_client.submit_order({
                "symbol": symbol,
                "side": "BUY",
                "quantity": quantity,
                "price": current_price
            })

            logger.success(
                f"\n✅ 开仓订单已提交: {order['order_id']}\n"
                f"   标的: {symbol}\n"
                f"   类型: {signal_type}\n"
                f"   评分: {signal['strength']:.0f}/100\n"
                f"   数量: {quantity}股\n"
                f"   价格: ${current_price:.2f}\n"
                f"   总额: ${required_cash:.2f}\n"
                f"   止损位: ${signal['stop_loss']:.2f}\n"
                f"   止盈位: ${signal['take_profit']:.2f}"
            )

            # 发送Slack通知
            if self.slack:
                emoji_map = {
                    'STRONG_BUY': '🚀',
                    'BUY': '📈',
                    'WEAK_BUY': '👍'
                }
                emoji = emoji_map.get(signal_type, '💰')

                # 构建详细的技术指标信息
                indicators_text = f"📊 *技术指标*:\n"
                if 'rsi' in signal:
                    indicators_text += f"   • RSI: {signal['rsi']:.1f}"
                    if signal['rsi'] < 30:
                        indicators_text += " (超卖 ⬇️)\n"
                    elif signal['rsi'] < 40:
                        indicators_text += " (偏低)\n"
                    else:
                        indicators_text += "\n"

                if 'macd' in signal and 'macd_signal' in signal:
                    macd_diff = signal['macd'] - signal['macd_signal']
                    indicators_text += f"   • MACD: {signal['macd']:.3f} | Signal: {signal['macd_signal']:.3f}\n"
                    if macd_diff > 0:
                        indicators_text += f"   • MACD差值: +{macd_diff:.3f} (金叉 ✅)\n"
                    else:
                        indicators_text += f"   • MACD差值: {macd_diff:.3f}\n"

                if 'bb_position' in signal:
                    indicators_text += f"   • 布林带位置: {signal['bb_position']:.1f}%"
                    if signal['bb_position'] < 20:
                        indicators_text += " (接近下轨 ⬇️)\n"
                    else:
                        indicators_text += "\n"

                if 'volume_ratio' in signal:
                    indicators_text += f"   • 成交量比率: {signal['volume_ratio']:.2f}x"
                    if signal['volume_ratio'] > 1.5:
                        indicators_text += " (放量 📈)\n"
                    else:
                        indicators_text += "\n"

                if 'trend' in signal:
                    trend_emoji = "📈" if signal['trend'] == 'bullish' else "📉" if signal['trend'] == 'bearish' else "➡️"
                    indicators_text += f"   • 趋势: {signal['trend']} {trend_emoji}\n"

                # 构建买入原因
                reasons = signal.get('reasons', [])
                reasons_text = "\n💡 *买入理由*:\n"
                for reason in reasons:
                    reasons_text += f"   • {reason}\n"

                message = (
                    f"{emoji} *开仓订单已提交*\n\n"
                    f"📋 订单ID: `{order['order_id']}`\n"
                    f"📊 标的: *{symbol}*\n"
                    f"💯 信号类型: {signal_type}\n"
                    f"⭐ 综合评分: *{signal['strength']:.0f}/100*\n\n"
                    f"💰 *交易信息*:\n"
                    f"   • 数量: {quantity}股\n"
                    f"   • 价格: ${current_price:.2f}\n"
                    f"   • 总额: ${required_cash:.2f}\n\n"
                    f"{indicators_text}\n"
                    f"🎯 *风控设置*:\n"
                    f"   • 止损位: ${signal['stop_loss']:.2f} ({(signal['stop_loss']/current_price-1)*100:.1f}%)\n"
                    f"   • 止盈位: ${signal['take_profit']:.2f} ({(signal['take_profit']/current_price-1)*100:.1f}%)\n"
                    f"   • ATR: ${signal['atr']:.2f}"
                )

                if reasons:
                    message += reasons_text

                await self.slack.send(message)

            # 记录止损止盈
            self.positions_with_stops[symbol] = {
                "entry_price": current_price,
                "stop_loss": signal['stop_loss'],
                "take_profit": signal['take_profit'],
                "atr": signal['atr']
            }

            # 标记为已交易
            self.executed_today.add(symbol)

        except Exception as e:
            logger.error(f"  ❌ {symbol} 开仓失败: {e}")
            import traceback
            traceback.print_exc()


async def main():
    """主函数"""
    import sys

    # 检查命令行参数
    use_builtin = "--builtin" in sys.argv or "-b" in sys.argv

    if use_builtin:
        logger.info("\n使用内置监控列表 - 高级技术指标组合策略")
    else:
        logger.info("\n使用配置文件监控列表 - 高级技术指标组合策略")

    trader = AdvancedTechnicalTrader(use_builtin_watchlist=use_builtin)

    try:
        await trader.run()
    except KeyboardInterrupt:
        logger.info("\n收到中断信号，停止交易系统")


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════════════╗
║         高级技术指标自动交易系统 v2.0                                 ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  📊 技术指标组合:                                                      ║
║     • RSI (相对强弱指标) - 超卖超买判断                                ║
║     • 布林带 (Bollinger Bands) - 价格位置分析                          ║
║     • MACD - 趋势确认和金叉死叉                                        ║
║     • 成交量分析 - 放量确认突破有效性                                   ║
║     • ATR - 波动率和动态止损止盈                                       ║
║     • 多周期SMA - 趋势方向确认                                         ║
║                                                                       ║
║  🎯 信号评分系统 (0-100分):                                            ║
║     • RSI分析: 0-30分                                                 ║
║     • 布林带分析: 0-25分                                               ║
║     • MACD分析: 0-20分                                                ║
║     • 成交量确认: 0-15分                                               ║
║     • 趋势确认: 0-10分                                                 ║
║                                                                       ║
║  ✅ 买入信号:                                                          ║
║     • 强买入 (≥60分): RSI超卖+布林带下轨+MACD金叉+放量                  ║
║     • 买入 (≥45分): 多个指标确认但强度较弱                              ║
║     • 弱买入 (≥30分): 少量指标支持                                     ║
║                                                                       ║
║  🛑 卖出/平仓信号:                                                     ║
║     • 触及止损位 (ATR × 2)                                             ║
║     • 触及止盈位 (ATR × 3)                                             ║
║     • RSI超买 + 突破布林带上轨                                         ║
║                                                                       ║
║  ⚙️  风控参数:                                                         ║
║     • 每只股票预算: $5,000                                             ║
║     • 最大持仓数量: 5只                                                ║
║     • 动态止损: 基于ATR自动计算                                        ║
║     • 每只股票每天最多交易1次                                           ║
║                                                                       ║
║  📋 监控列表:                                                          ║
║     • 默认: 从 configs/watchlist.yml 加载                              ║
║     • 内置: 50+个港股 + 8个美股 (使用 --builtin 参数)                   ║
║                                                                       ║
║  🚀 启动命令:                                                          ║
║     python3 scripts/advanced_technical_trading.py                    ║
║     python3 scripts/advanced_technical_trading.py --builtin          ║
║                                                                       ║
║  按 Ctrl+C 停止                                                       ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
    """)

    asyncio.run(main())