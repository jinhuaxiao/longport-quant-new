#!/usr/bin/env python3
"""增强版交易策略 v2.0 - 实时订阅模式 + 市场时间判断"""

import asyncio
from datetime import datetime, timedelta, time
from decimal import Decimal
from zoneinfo import ZoneInfo
from loguru import logger
import numpy as np
from typing import Dict, List, Optional, Set
import json

from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.execution.client import LongportTradingClient
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.features.technical_indicators import TechnicalIndicators
from longport_quant.notifications.slack import SlackNotifier
from longport_quant.utils.trading import LotSizeHelper
from longport_quant.persistence.order_manager import OrderManager


class EnhancedTradingStrategy:
    """增强版交易策略：实时订阅 + 双策略系统"""

    def __init__(self, use_builtin_watchlist=False, enable_trading=True, enable_slack=True, limit_positions=False):
        """初始化交易系统"""
        self.settings = get_settings()
        self.beijing_tz = ZoneInfo('Asia/Shanghai')
        self.hongkong_tz = ZoneInfo('Asia/Hong_Kong')
        self.newyork_tz = ZoneInfo('America/New_York')

        self.enable_trading = enable_trading
        self.enable_slack = enable_slack
        self.slack = None
        self.use_builtin_watchlist = use_builtin_watchlist
        self.limit_positions = limit_positions  # 是否限制持仓数量

        # 保存主事件循环的引用，用于回调
        self.main_loop = None

        # 港股监控列表
        self.hk_watchlist = {
            # 科技股
            "9988.HK": {"name": "阿里巴巴", "sector": "科技"},
            "3690.HK": {"name": "美团", "sector": "科技"},
            "0700.HK": {"name": "腾讯", "sector": "科技"},
            "1810.HK": {"name": "小米", "sector": "科技"},
            "9618.HK": {"name": "京东", "sector": "科技"},
            "1024.HK": {"name": "快手", "sector": "科技"},

            # 半导体
            "0981.HK": {"name": "中芯国际", "sector": "半导体"},
            "1347.HK": {"name": "华虹半导体", "sector": "半导体"},

            # 新能源汽车
            "1211.HK": {"name": "比亚迪", "sector": "汽车"},
            "9868.HK": {"name": "小鹏汽车", "sector": "汽车"},
            "2015.HK": {"name": "理想汽车", "sector": "汽车"},

            # ETF
            "2800.HK": {"name": "盈富基金", "sector": "ETF"},
            "2828.HK": {"name": "恒生中国企业", "sector": "ETF"},
            "3067.HK": {"name": "安硕恒生科技", "sector": "ETF"},
        }

        # 美股监控列表
        self.us_watchlist = {
            "AAPL.US": {"name": "苹果", "sector": "科技"},
            "MSFT.US": {"name": "微软", "sector": "科技"},
            "NVDA.US": {"name": "英伟达", "sector": "科技"},
            "TSLA.US": {"name": "特斯拉", "sector": "汽车"},
            "AMD.US": {"name": "AMD", "sector": "科技"},
            "GOOGL.US": {"name": "谷歌", "sector": "科技"},
            "META.US": {"name": "Meta", "sector": "科技"},
            "AMZN.US": {"name": "亚马逊", "sector": "科技"},
        }

        # 策略参数
        self.strategy_mode = "HYBRID"  # REVERSAL, BREAKOUT, HYBRID

        # 交易参数
        self.max_positions = 10
        self.min_position_size_pct = 0.05
        self.max_position_size_pct = 0.30
        self.max_daily_trades_per_symbol = 3

        # 逆势策略参数
        self.rsi_oversold = 30
        self.rsi_overbought = 70
        self.bb_period = 20
        self.bb_std = 2.0

        # 突破策略参数
        self.breakout_lookback = 20
        self.volume_breakout_multiplier = 1.8
        self.resistance_tolerance = 0.02
        self.momentum_period = 10
        self.roc_threshold = 5

        # 止损止盈参数
        self.atr_period = 14
        self.reversal_stop_multiplier = 1.8
        self.reversal_profit_multiplier = 2.5
        self.breakout_stop_multiplier = 1.5
        self.breakout_profit_multiplier = 3.5

        # 信号阈值
        self.strong_signal_threshold = 55
        self.normal_signal_threshold = 40
        self.weak_signal_threshold = 25

        # 状态管理
        self.positions_with_stops = {}
        self.signal_history = {}
        self.subscribed_symbols: Set[str] = set()
        self.last_analysis_time = {}
        self.min_analysis_interval = 30  # 最小分析间隔（秒）

        # 信号队列（优先级队列）
        self.signal_queue = asyncio.PriorityQueue()
        self.signal_counter = 0  # 用于生成唯一的信号ID，避免优先级相同时的比较错误

        # 辅助工具
        self.lot_size_helper = LotSizeHelper()
        self.order_manager = OrderManager()

        logger.info("=" * 60)
        logger.info("🚀 初始化增强版交易策略 V2.0")
        logger.info(f"   策略模式: {self.strategy_mode}")
        logger.info(f"   • 实时订阅模式")
        logger.info(f"   • 市场时间判断")
        logger.info(f"   • 逆势买入: RSI超卖 + 布林带下轨")
        logger.info(f"   • 突破买入: 价格突破 + 成交量确认")
        logger.info("=" * 60)

    def _normalize_hk_symbol(self, symbol):
        """标准化港股代码"""
        if symbol.endswith('.HK'):
            code = symbol[:-3]
            if len(code) < 4 and code.isdigit():
                code = code.zfill(4)
                return f"{code}.HK"
        return symbol

    def _get_symbol_name(self, symbol):
        """获取标的中文名称"""
        normalized = self._normalize_hk_symbol(symbol)
        if normalized in self.hk_watchlist:
            return self.hk_watchlist[normalized]["name"]
        elif symbol in self.us_watchlist:
            return self.us_watchlist[symbol]["name"]
        return ""

    def is_trading_hours(self, symbol: str) -> tuple[bool, str]:
        """
        检查当前是否为交易时间
        返回: (是否交易时间, 市场状态描述)
        """
        now = datetime.now()

        if ".HK" in symbol:
            # 香港时间
            hk_now = now.astimezone(self.hongkong_tz)
            hk_time = hk_now.time()
            weekday = hk_now.weekday()

            # 周末不交易
            if weekday >= 5:
                return False, "港股周末休市"

            # 早盘: 09:30-12:00
            if time(9, 30) <= hk_time < time(12, 0):
                return True, "港股早盘"

            # 午盘: 13:00-16:00
            if time(13, 0) <= hk_time < time(16, 0):
                return True, "港股午盘"

            # 其他时间
            if hk_time < time(9, 30):
                return False, "港股盘前"
            elif time(12, 0) <= hk_time < time(13, 0):
                return False, "港股午休"
            else:
                return False, "港股盘后"

        elif ".US" in symbol:
            # 纽约时间
            ny_now = now.astimezone(self.newyork_tz)
            ny_time = ny_now.time()
            weekday = ny_now.weekday()

            # 周末不交易
            if weekday >= 5:
                return False, "美股周末休市"

            # 盘前: 04:00-09:30
            if time(4, 0) <= ny_time < time(9, 30):
                return True, "美股盘前"

            # 正常交易: 09:30-16:00
            if time(9, 30) <= ny_time < time(16, 0):
                return True, "美股正常交易"

            # 盘后: 16:00-20:00
            if time(16, 0) <= ny_time < time(20, 0):
                return True, "美股盘后"

            # 其他时间
            return False, "美股休市"

        else:
            # 未知市场，默认可交易
            return True, "未知市场"

    def on_quote_update(self, symbol: str, event: openapi.PushQuote):
        """
        处理实时行情推送（同步函数，使用run_coroutine_threadsafe调度到主循环）
        注意: 这是回调函数，在独立线程中运行，需要安全地调度到主事件循环
        """
        try:
            # 安全地在主事件循环中调度异步任务
            if self.main_loop and not self.main_loop.is_closed():
                asyncio.run_coroutine_threadsafe(
                    self._handle_quote_update(symbol, event),
                    self.main_loop
                )
            else:
                logger.debug(f"主事件循环不可用，跳过行情 {symbol}")
        except Exception as e:
            logger.error(f"调度行情处理任务失败 {symbol}: {e}")

    async def _handle_quote_update(self, symbol: str, event: openapi.PushQuote):
        """异步处理行情更新"""
        try:
            # 检查市场是否开盘
            is_open, market_status = self.is_trading_hours(symbol)

            # 对于已持仓标的，即使非交易时间也要检查止损止盈
            account = await self.check_account_status()
            is_holding = symbol in account['positions']

            if not is_open and not is_holding:
                # 非交易时间且未持仓，跳过
                return

            # 打印调试信息（每个标的第一次）
            if symbol not in self.last_analysis_time:
                logger.info(f"📊 {symbol}: {market_status}, 持仓={is_holding}")

            # 检查是否需要分析（避免过于频繁）
            current_time = datetime.now()
            last_time = self.last_analysis_time.get(symbol)

            if last_time and (current_time - last_time).total_seconds() < self.min_analysis_interval:
                return

            self.last_analysis_time[symbol] = current_time

            # 获取当前价格
            current_price = float(event.last_done) if event.last_done else 0
            if current_price <= 0:
                return

            # 异步分析信号
            await self.analyze_realtime_signal(symbol, current_price, event, market_status, is_holding)

        except Exception as e:
            logger.debug(f"处理行情推送失败 {symbol}: {e}")

    async def analyze_realtime_signal(self, symbol, current_price, quote, market_status, is_holding=False):
        """实时分析交易信号"""
        try:
            # 获取账户状态
            account = await self.check_account_status()

            if is_holding and symbol in account['positions']:
                # 检查止损止盈
                await self._check_exit_signals(symbol, current_price, account['positions'][symbol])
            elif not is_holding:
                # 检查是否限制持仓数量
                if self.limit_positions and account['position_count'] >= self.max_positions:
                    return  # 达到持仓限制，跳过

                # 美股盘前盘后降低仓位
                position_multiplier = 1.0
                if ".US" in symbol and ("盘前" in market_status or "盘后" in market_status):
                    position_multiplier = 0.7  # 盘前盘后仓位降低30%

                signal = await self.analyze_combined_signals(symbol, current_price, quote)

                if signal and signal['score'] >= self.weak_signal_threshold:
                    # 添加仓位调整
                    signal['position_multiplier'] = position_multiplier
                    signal['market_status'] = market_status

                    # 计算优先级（分数越高，优先级越高）
                    priority = -signal['score']  # 负数，因为PriorityQueue是最小堆

                    # 生成唯一ID避免优先级相同时的字典比较错误
                    self.signal_counter += 1

                    # 加入信号队列 (priority, counter, data)
                    await self.signal_queue.put((
                        priority,
                        self.signal_counter,
                        {
                            'symbol': symbol,
                            'signal': signal,
                            'price': current_price,
                            'timestamp': datetime.now()
                        }
                    ))

                    name = self._get_symbol_name(symbol)
                    logger.info(
                        f"📊 [{market_status}] {symbol}({name}): "
                        f"{signal['strength']} {signal['strategy']} 信号 "
                        f"(评分:{signal['score']})"
                    )

        except Exception as e:
            logger.debug(f"实时分析失败 {symbol}: {e}")

    async def _check_exit_signals(self, symbol, current_price, position):
        """检查止损止盈信号"""
        if symbol not in self.positions_with_stops:
            return

        stops = self.positions_with_stops[symbol]
        entry_price = position['cost']
        pnl_pct = (current_price / entry_price - 1) * 100

        # 止损检查
        if current_price <= stops['stop_loss']:
            priority = -100  # 止损最高优先级
            self.signal_counter += 1
            await self.signal_queue.put((
                priority,
                self.signal_counter,
                {
                    'symbol': symbol,
                    'type': 'STOP_LOSS',
                    'position': position,
                    'price': current_price,
                    'reason': '止损',
                    'pnl_pct': pnl_pct
                }
            ))
            logger.warning(f"🛑 {symbol} 触发止损! 盈亏: {pnl_pct:.2f}%")

        # 止盈检查
        elif current_price >= stops['take_profit']:
            priority = -90  # 止盈次高优先级
            self.signal_counter += 1
            await self.signal_queue.put((
                priority,
                self.signal_counter,
                {
                    'symbol': symbol,
                    'type': 'TAKE_PROFIT',
                    'position': position,
                    'price': current_price,
                    'reason': '止盈',
                    'pnl_pct': pnl_pct
                }
            ))
            logger.success(f"🎉 {symbol} 触发止盈! 盈亏: {pnl_pct:.2f}%")

    async def signal_processor(self):
        """信号处理器 - 按优先级处理信号队列"""
        logger.info("🚀 启动信号处理器...")

        while True:
            try:
                # 从优先级队列获取信号 (priority, counter, data)
                priority, counter, signal_data = await self.signal_queue.get()

                symbol = signal_data['symbol']
                signal_type = signal_data.get('type', '')
                current_price = signal_data['price']

                # 处理止损止盈信号
                if signal_type in ['STOP_LOSS', 'TAKE_PROFIT']:
                    position = signal_data['position']
                    reason = signal_data['reason']
                    pnl_pct = signal_data['pnl_pct']

                    logger.info(f"\n🚨 处理{reason}信号: {symbol}, 盈亏: {pnl_pct:+.2f}%")
                    await self.execute_sell(symbol, current_price, position, reason)
                    continue

                # 处理买入信号
                signal = signal_data.get('signal')
                if signal:
                    # 再次检查市场时间（信号可能在队列中等待）
                    is_open, market_status = self.is_trading_hours(symbol)
                    if not is_open:
                        logger.debug(f"   {symbol}: {market_status}，跳过信号")
                        continue

                    logger.info(f"\n📌 处理买入信号: {symbol}, 评分={signal['score']}, 市场={signal.get('market_status', '')}")

                    # 重新检查账户状态
                    account = await self.check_account_status()

                    # 检查是否可以开仓（移除数量限制，只检查是否已持有）
                    if symbol not in account['positions']:
                        await self.execute_signal(signal, account)
                    else:
                        logger.debug(f"   {symbol}: 已持有，跳过")

            except asyncio.QueueEmpty:
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"信号处理器错误: {e}")
                await asyncio.sleep(1)

    async def analyze_combined_signals(self, symbol, current_price, quote):
        """综合分析买入信号"""
        try:
            # 获取历史数据
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

            # 计算技术指标
            closes = np.array([float(c.close) for c in candles])
            highs = np.array([float(c.high) for c in candles])
            lows = np.array([float(c.low) for c in candles])
            volumes = np.array([float(c.volume) for c in candles])

            # 基础指标
            ind = {}
            ind['rsi'] = TechnicalIndicators.rsi(closes, period=14)[-1]

            bb = TechnicalIndicators.bollinger_bands(closes, period=self.bb_period, num_std=self.bb_std)
            ind['bb_upper'] = bb['upper'][-1]
            ind['bb_middle'] = bb['middle'][-1]
            ind['bb_lower'] = bb['lower'][-1]

            # MACD
            macd_result = TechnicalIndicators.macd(closes, fast_period=12, slow_period=26, signal_period=9)
            ind['macd_histogram'] = macd_result['histogram'][-1]
            ind['prev_macd_histogram'] = macd_result['histogram'][-2] if len(macd_result['histogram']) > 1 else 0

            # 均线和成交量
            ind['sma_20'] = np.mean(closes[-20:]) if len(closes) >= 20 else 0
            ind['sma_50'] = np.mean(closes[-50:]) if len(closes) >= 50 else 0
            ind['volume_sma'] = np.mean(volumes[-20:]) if len(volumes) >= 20 else 0

            # ATR用于止损
            atr = TechnicalIndicators.atr(highs, lows, closes, period=self.atr_period)
            ind['atr'] = atr[-1] if len(atr) > 0 else 0

            # 分析信号
            reversal_signal = self._analyze_reversal_signal(symbol, current_price, ind, quote)
            breakout_signal = self._analyze_breakout_signal(symbol, current_price, ind, quote, highs, lows, volumes)

            # 选择最佳信号
            best_signal = None
            if self.strategy_mode == "REVERSAL":
                best_signal = reversal_signal
            elif self.strategy_mode == "BREAKOUT":
                best_signal = breakout_signal
            elif self.strategy_mode == "HYBRID":
                best_signal = reversal_signal if reversal_signal['score'] >= breakout_signal['score'] else breakout_signal

            # 添加信号信息
            if best_signal and best_signal['score'] > 0:
                best_signal['symbol'] = symbol
                best_signal['price'] = current_price
                best_signal['rsi'] = ind['rsi']
                best_signal['atr'] = ind['atr']

                # 设置止损止盈
                if best_signal['strategy'] == 'REVERSAL':
                    best_signal['stop_loss'] = current_price - ind['atr'] * self.reversal_stop_multiplier
                    best_signal['take_profit'] = current_price + ind['atr'] * self.reversal_profit_multiplier
                else:
                    best_signal['stop_loss'] = current_price - ind['atr'] * self.breakout_stop_multiplier
                    best_signal['take_profit'] = current_price + ind['atr'] * self.breakout_profit_multiplier

                # 判断信号强度
                if best_signal['score'] >= self.strong_signal_threshold:
                    best_signal['strength'] = 'STRONG'
                elif best_signal['score'] >= self.normal_signal_threshold:
                    best_signal['strength'] = 'NORMAL'
                elif best_signal['score'] >= self.weak_signal_threshold:
                    best_signal['strength'] = 'WEAK'
                else:
                    return None

                return best_signal

        except Exception as e:
            logger.error(f"综合信号分析失败 {symbol}: {e}")

        return None

    def _analyze_reversal_signal(self, symbol, current_price, ind, quote):
        """分析逆势买入信号"""
        score = 0
        reasons = []

        # RSI分析
        if ind['rsi'] < 20:
            score += 30
            reasons.append(f"RSI极度超卖({ind['rsi']:.1f})")
        elif ind['rsi'] < self.rsi_oversold:
            score += 25
            reasons.append(f"RSI超卖({ind['rsi']:.1f})")
        elif ind['rsi'] < 40:
            score += 15

        # 布林带分析
        if current_price <= ind['bb_lower']:
            score += 25
            reasons.append("触及布林带下轨")
        elif current_price <= ind['bb_lower'] * 1.02:
            score += 20
            reasons.append("接近布林带下轨")

        # MACD分析
        if ind['macd_histogram'] > 0 and ind.get('prev_macd_histogram', 0) <= 0:
            score += 20
            reasons.append("MACD金叉")

        # 成交量确认
        volume_ratio = float(quote.volume) / float(ind['volume_sma']) if ind['volume_sma'] > 0 else 1.0
        if volume_ratio >= 1.5:
            score += 15
            reasons.append(f"成交量放大({volume_ratio:.1f}x)")

        # 趋势确认
        if ind['sma_20'] > ind['sma_50']:
            score += 10
            reasons.append("上升趋势")

        return {
            'type': 'REVERSAL',
            'score': score,
            'reasons': reasons,
            'strategy': 'REVERSAL'
        }

    def _analyze_breakout_signal(self, symbol, current_price, ind, quote, highs, lows, volumes):
        """分析突破买入信号"""
        score = 0
        reasons = []

        # 价格突破
        recent_high = np.max(highs[-self.breakout_lookback:-1]) if len(highs) > self.breakout_lookback else 0
        if recent_high > 0 and current_price > recent_high:
            score += 30
            reasons.append(f"突破{self.breakout_lookback}日新高")
        elif recent_high > 0 and current_price > recent_high * (1 - self.resistance_tolerance):
            score += 20
            reasons.append("接近突破位")

        # 成交量突破
        volume_ratio = float(quote.volume) / float(ind['volume_sma']) if ind['volume_sma'] > 0 else 1.0
        if volume_ratio >= self.volume_breakout_multiplier:
            score += 25
            reasons.append(f"成交量突破({volume_ratio:.1f}倍)")

        # 动量分析
        if len(highs) >= self.momentum_period:
            roc = ((current_price - highs[-self.momentum_period]) / highs[-self.momentum_period]) * 100
            if roc > self.roc_threshold * 2:
                score += 20
                reasons.append(f"强势动量(ROC:{roc:.1f}%)")
            elif roc > self.roc_threshold:
                score += 12
                reasons.append(f"正面动量(ROC:{roc:.1f}%)")

        # 趋势强度
        if ind['sma_20'] > ind['sma_50']:
            score += 10
            reasons.append("上升趋势")

        # RSI确认
        if 50 < ind['rsi'] < 70:
            score += 5
            reasons.append(f"RSI健康({ind['rsi']:.0f})")

        return {
            'type': 'BREAKOUT',
            'score': score,
            'reasons': reasons,
            'strategy': 'BREAKOUT'
        }

    async def check_account_status(self):
        """检查账户状态"""
        balances = await self.trade_client.account_balance()
        positions_resp = await self.trade_client.stock_positions()

        buy_power = {}
        total_cash = {}

        for balance in balances:
            currency = balance.currency
            buy_power[currency] = float(balance.buy_power) if hasattr(balance, 'buy_power') else 0
            total_cash[currency] = float(balance.total_cash) if hasattr(balance, 'total_cash') else 0

        positions = {}
        for channel in positions_resp.channels:
            for pos in channel.positions:
                symbol = self._normalize_hk_symbol(pos.symbol)
                positions[symbol] = {
                    "quantity": pos.quantity,
                    "cost": float(pos.cost_price) if pos.cost_price else 0,
                    "currency": pos.currency,
                    "market": pos.market
                }

        return {
            "buy_power": buy_power,
            "cash": total_cash,
            "positions": positions,
            "position_count": len(positions)
        }

    async def execute_signal(self, signal, account):
        """执行买入信号"""
        symbol = signal['symbol']

        # 确定交易货币
        currency = "HKD" if symbol.endswith('.HK') else "USD"

        # 获取购买力
        available_power = account['buy_power'].get(currency, 0)

        # 跨币种处理
        use_hkd_for_usd = False
        if currency == "USD" and available_power < 1000:
            hkd_power = account['buy_power'].get("HKD", 0)
            if hkd_power > 0:
                available_power = hkd_power / 7.8
                use_hkd_for_usd = True

        # 检查资金
        min_amount = 1000 if currency == "USD" else 10000
        if available_power < min_amount:
            logger.warning(f"   {symbol}: 购买力不足")
            return False

        # 计算仓位（考虑美股盘前盘后调整）
        position_multiplier = signal.get('position_multiplier', 1.0)

        if signal['strength'] == 'STRONG':
            position_size = available_power * 0.15 * position_multiplier
        elif signal['strength'] == 'NORMAL':
            position_size = available_power * 0.10 * position_multiplier
        else:
            position_size = available_power * 0.08 * position_multiplier

        position_size = min(position_size, available_power * self.max_position_size_pct)

        # 计算数量
        price_for_calculation = signal['price'] * 7.8 if use_hkd_for_usd else signal['price']
        quantity = int(position_size / price_for_calculation)

        # 调整到最小交易单位
        if symbol.endswith('.HK'):
            lot_size = 100
            quantity = (quantity // lot_size) * lot_size
        quantity = max(1, int(quantity))

        if quantity <= 0:
            return False

        try:
            market_status = signal.get('market_status', '')
            logger.info(f"\n📈 [{market_status}] 执行{signal['strategy']}买入信号:")
            logger.info(f"   标的: {symbol} ({self._get_symbol_name(symbol)})")
            logger.info(f"   价格: ${signal['price']:.2f}")
            logger.info(f"   数量: {quantity}")
            logger.info(f"   原因: {', '.join(signal['reasons'][:3])}")

            logger.debug(f"   enable_trading={self.enable_trading}")
            if self.enable_trading:
                order_request = {
                    "symbol": symbol,
                    "side": "BUY",
                    "quantity": quantity,
                    "price": signal['price'],
                }

                logger.info(f"   📤 正在提交订单...")
                try:
                    # 添加超时保护，防止API调用挂起
                    order_response = await asyncio.wait_for(
                        self.trade_client.submit_order(order_request),
                        timeout=10.0  # 10秒超时
                    )
                    order_id = order_response.get("order_id")
                    logger.success(f"   ✅ 订单提交成功 (ID: {order_id})")
                except asyncio.TimeoutError:
                    logger.error(f"   ❌ 订单提交超时（10秒）")
                    raise
                except Exception as e:
                    logger.error(f"   ❌ 订单提交异常: {type(e).__name__}: {e}")
                    raise

                # 记录订单
                await self.order_manager.save_order(
                    order_id=order_id,
                    symbol=symbol,
                    side="BUY",
                    quantity=quantity,
                    price=signal['price'],
                    status="New"
                )
            else:
                logger.info("   ⚠️ 模拟模式，不执行真实下单")

            # 记录止损止盈
            self.positions_with_stops[symbol] = {
                "entry_price": signal['price'],
                "stop_loss": signal['stop_loss'],
                "take_profit": signal['take_profit'],
                "strategy": signal['strategy'],
                "entry_time": datetime.now(),
                "atr": signal.get('atr', 0)
            }

            # 发送Slack通知
            logger.debug(f"   Slack enabled: {self.slack is not None}")
            if self.slack:
                try:
                    name = self._get_symbol_name(symbol)
                    message = (
                        f"*{signal['strategy']}买入执行*\n"
                        f"• {symbol} ({name}): ${signal['price']:.2f} × {quantity}\n"
                        f"• 评分: {signal['score']}\n"
                        f"• 止损/止盈: ${signal['stop_loss']:.2f} / ${signal['take_profit']:.2f}"
                    )
                    logger.info(f"   📨 正在发送Slack通知...")
                    await asyncio.wait_for(self.slack.send(message), timeout=5.0)
                    logger.success(f"   ✅ Slack通知已发送")
                except asyncio.TimeoutError:
                    logger.warning(f"   ⚠️ Slack通知发送超时")
                except Exception as e:
                    logger.warning(f"   ⚠️ Slack通知发送失败: {e}")

            return True

        except Exception as e:
            logger.error(f"   ❌ 下单失败: {e}")
            return False

    async def execute_sell(self, symbol, current_price, position, reason):
        """执行卖出"""
        try:
            quantity = position['quantity']

            logger.info(f"\n📉 执行{reason}卖出:")
            logger.info(f"   标的: {symbol}")
            logger.info(f"   价格: ${current_price:.2f}")
            logger.info(f"   数量: {quantity}")

            if self.enable_trading:
                order_request = {
                    "symbol": symbol,
                    "side": "SELL",
                    "quantity": quantity,
                    "price": current_price,
                }

                order_response = await self.trade_client.submit_order(order_request)
                order_id = order_response.get("order_id")
                logger.success(f"   ✅ 卖出订单提交成功 (ID: {order_id})")

                await self.order_manager.save_order(
                    order_id=order_id,
                    symbol=symbol,
                    side="SELL",
                    quantity=quantity,
                    price=current_price,
                    status="New"
                )

            # 清理记录
            if symbol in self.positions_with_stops:
                del self.positions_with_stops[symbol]

            # 发送通知
            if self.slack:
                entry_price = position['cost']
                pnl_pct = (current_price / entry_price - 1) * 100
                emoji = "🛑" if reason == "止损" else "🎉"

                message = (
                    f"{emoji} *{reason}卖出执行*\n"
                    f"• {symbol}: ${current_price:.2f} × {quantity}\n"
                    f"• 盈亏: {pnl_pct:+.2f}%"
                )
                await self.slack.send(message)

            return True

        except Exception as e:
            logger.error(f"   ❌ 卖出失败: {e}")
            return False

    async def market_status_monitor(self):
        """市场状态监控器 - 定期显示各市场状态"""
        while True:
            try:
                await asyncio.sleep(300)  # 每5分钟检查一次

                # 检查各市场状态
                status_info = []

                # 港股状态
                sample_hk = "0700.HK"
                hk_open, hk_status = self.is_trading_hours(sample_hk)
                status_info.append(f"🇭🇰 港股: {hk_status}")

                # 美股状态
                sample_us = "AAPL.US"
                us_open, us_status = self.is_trading_hours(sample_us)
                status_info.append(f"🇺🇸 美股: {us_status}")

                logger.info(f"\n📍 市场状态: {' | '.join(status_info)}")

            except Exception as e:
                logger.error(f"市场状态监控错误: {e}")

    async def run(self):
        """主运行循环 - 使用实时订阅"""
        logger.info("\n" + "=" * 60)
        logger.info("🚀 启动增强版交易策略 V2.0")
        logger.info("=" * 60)

        # 显示当前时间和市场状态
        now = datetime.now()
        logger.info(f"📅 当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")

        # 保存当前事件循环的引用
        self.main_loop = asyncio.get_running_loop()

        # 初始化客户端
        self.quote_client = QuoteDataClient(self.settings)
        self.trade_client = LongportTradingClient(self.settings)

        # 初始化Slack
        if self.enable_slack:
            try:
                webhook_url = self.settings.slack_webhook_url if hasattr(self.settings, 'slack_webhook_url') else None
                if webhook_url:
                    self.slack = SlackNotifier(webhook_url)
                    logger.info("✅ Slack通知已启用")
                else:
                    logger.warning("⚠️ Slack webhook URL未配置")
                    self.slack = None
            except Exception as e:
                logger.warning(f"⚠️ Slack通知初始化失败: {e}")
                self.slack = None

        # 获取监控列表
        if self.use_builtin_watchlist:
            symbols = list(self.hk_watchlist.keys()) + list(self.us_watchlist.keys())
            logger.info(f"使用内置监控列表: {len(symbols)}个标的")
        else:
            loader = WatchlistLoader()
            watchlist = loader.load_watchlist()
            symbols = watchlist.get('symbols', [])
            logger.info(f"从配置文件加载: {len(symbols)}个标的")

        # 设置实时行情回调（使用同步函数）
        await self.quote_client.set_on_quote(self.on_quote_update)

        # 订阅实时行情
        logger.info(f"📡 订阅实时行情: {len(symbols)}个标的...")
        try:
            await self.quote_client.subscribe(
                symbols=symbols,
                sub_types=[openapi.SubType.Quote],  # 订阅报价数据
                is_first_push=True  # 订阅后立即推送一次数据
            )
            self.subscribed_symbols = set(symbols)
            logger.success(f"✅ 成功订阅 {len(symbols)} 个标的的实时行情")
        except Exception as e:
            logger.error(f"❌ 订阅失败: {e}")
            self.subscribed_symbols = set()

        # 启动信号处理器
        processor_task = asyncio.create_task(self.signal_processor())

        # 启动市场状态监控器
        monitor_task = asyncio.create_task(self.market_status_monitor())

        # 主循环 - 定期检查账户和更新订阅
        try:
            while True:
                # 定期更新账户状态
                account = await self.check_account_status()

                logger.info(f"\n📊 账户状态更新:")
                logger.info(f"   持仓数: {account['position_count']} (无限制)")
                logger.info(f"   购买力: HKD ${account['buy_power'].get('HKD', 0):,.0f}, USD ${account['buy_power'].get('USD', 0):,.0f}")

                # 初始化现有持仓的止损止盈（如果还没有设置）
                for symbol, position in account['positions'].items():
                    if symbol not in self.positions_with_stops:
                        # 使用默认的ATR倍数设置止损止盈
                        entry_price = position['cost']
                        # 假设ATR为价格的2%
                        estimated_atr = entry_price * 0.02
                        self.positions_with_stops[symbol] = {
                            "entry_price": entry_price,
                            "stop_loss": entry_price - estimated_atr * self.breakout_stop_multiplier,
                            "take_profit": entry_price + estimated_atr * self.breakout_profit_multiplier,
                            "strategy": "EXISTING",
                            "entry_time": datetime.now(),
                            "atr": estimated_atr
                        }
                        logger.info(f"   📌 初始化{symbol}止损止盈: 止损=${self.positions_with_stops[symbol]['stop_loss']:.2f}, 止盈=${self.positions_with_stops[symbol]['take_profit']:.2f}")

                # 动态订阅新的持仓（如果有）
                new_positions = set(account['positions'].keys()) - self.subscribed_symbols
                if new_positions:
                    logger.info(f"📡 动态订阅新持仓: {new_positions}")
                    try:
                        await self.quote_client.subscribe(
                            symbols=list(new_positions),
                            sub_types=[openapi.SubType.Quote],
                            is_first_push=True
                        )
                        self.subscribed_symbols.update(new_positions)
                    except Exception as e:
                        logger.error(f"动态订阅失败: {e}")

                # 等待下一次检查
                await asyncio.sleep(60)

        except KeyboardInterrupt:
            logger.info("\n⏹️ 收到停止信号，正在关闭...")
        finally:
            # 取消订阅
            if self.subscribed_symbols:
                try:
                    await self.quote_client.unsubscribe(
                        symbols=list(self.subscribed_symbols),
                        sub_types=[openapi.SubType.Quote]
                    )
                    logger.info("✅ 已取消所有订阅")
                except:
                    pass

            # 取消处理器任务
            processor_task.cancel()
            monitor_task.cancel()

            # 清理Slack
            if self.slack:
                await self.slack.__aexit__(None, None, None)


async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='增强版交易策略 V2.0')
    parser.add_argument('--builtin', action='store_true', help='使用内置监控列表')
    parser.add_argument('--mode', choices=['REVERSAL', 'BREAKOUT', 'HYBRID'],
                       default='HYBRID', help='策略模式')
    parser.add_argument('--dry-run', action='store_true', help='模拟运行')
    parser.add_argument('--no-slack', action='store_true', help='禁用Slack通知')
    args = parser.parse_args()

    # 创建策略实例
    strategy = EnhancedTradingStrategy(
        use_builtin_watchlist=args.builtin,
        enable_trading=not args.dry_run,
        enable_slack=not args.no_slack
    )
    strategy.strategy_mode = args.mode

    # 显示运行模式
    logger.info(f"运行模式: {'模拟' if args.dry_run else '实盘'}")
    logger.info(f"Slack通知: {'禁用' if args.no_slack else '启用'}")
    logger.info(f"策略模式: {args.mode}")

    await strategy.run()


if __name__ == "__main__":
    asyncio.run(main())