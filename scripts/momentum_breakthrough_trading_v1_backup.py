#!/usr/bin/env python3
"""增强版交易策略 - 逆势买入 + 突破买入双策略"""

import asyncio
from datetime import datetime, timedelta, time
from decimal import Decimal
from zoneinfo import ZoneInfo
from loguru import logger
import numpy as np
from typing import Dict, List, Optional
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
    """增强版交易策略：逆势 + 突破双策略"""

    def __init__(self, use_builtin_watchlist=False, enable_trading=True, enable_slack=True):
        """初始化交易系统"""
        self.settings = get_settings()
        self.beijing_tz = ZoneInfo('Asia/Shanghai')
        self.enable_trading = enable_trading  # 是否真实下单
        settings_flag = bool(getattr(self.settings, 'slack_enabled', True))
        self.enable_slack = enable_slack and settings_flag      # 是否发送Slack通知
        self.slack = None
        self.use_builtin_watchlist = use_builtin_watchlist

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

        # === 策略参数 ===
        self.strategy_mode = "HYBRID"  # REVERSAL(逆势), BREAKOUT(突破), HYBRID(混合)

        # 交易参数
        self.max_positions = 10
        self.min_position_size_pct = 0.05
        self.max_position_size_pct = 0.30
        self.max_daily_trades_per_symbol = 3  # 增加到3次

        # 逆势策略参数（原有）
        self.rsi_oversold = 30
        self.rsi_overbought = 70
        self.bb_period = 20
        self.bb_std = 2.0

        # === 突破策略参数（新增）===
        self.breakout_lookback = 20  # 突破回看天数
        self.volume_breakout_multiplier = 1.8  # 突破时成交量放大倍数
        self.breakout_confirmation_bars = 2  # 突破确认K线数
        self.resistance_tolerance = 0.02  # 阻力位容差 2%

        # 动量指标参数
        self.momentum_period = 10  # 动量周期
        self.roc_threshold = 5  # ROC变化率阈值 5%

        # 通道突破参数
        self.donchian_period = 20  # 唐奇安通道周期
        self.keltner_period = 20  # 肯特纳通道周期
        self.keltner_atr_multiplier = 2.0

        # 止损止盈参数（调整）
        self.atr_period = 14
        self.reversal_stop_multiplier = 1.8  # 逆势策略止损（更紧）
        self.reversal_profit_multiplier = 2.5  # 逆势策略止盈
        self.breakout_stop_multiplier = 1.5  # 突破策略止损（更紧）
        self.breakout_profit_multiplier = 3.5  # 突破策略止盈（更宽）

        # 信号阈值（调整）
        self.strong_signal_threshold = 55  # 降低强信号阈值
        self.normal_signal_threshold = 40  # 降低普通信号阈值
        self.weak_signal_threshold = 25    # 降低弱信号阈值

        # 持仓管理
        self.positions_with_stops = {}
        self.signal_history = {}  # 记录信号历史避免重复

        # 辅助工具
        self.lot_size_helper = LotSizeHelper()
        self.order_manager = OrderManager()

        logger.info("=" * 60)
        logger.info("🚀 初始化增强版交易策略")
        logger.info(f"   策略模式: {self.strategy_mode}")
        logger.info(f"   • 逆势买入: RSI超卖 + 布林带下轨")
        logger.info(f"   • 突破买入: 价格突破 + 成交量确认")
        logger.info("=" * 60)

    def _normalize_hk_symbol(self, symbol):
        """标准化港股代码格式"""
        if symbol.endswith('.HK'):
            code = symbol[:-3]
            if len(code) < 4 and code.isdigit():
                code = code.zfill(4)
                return f"{code}.HK"
        return symbol

    def _get_symbol_name(self, symbol):
        """获取标的的中文名称"""
        normalized = self._normalize_hk_symbol(symbol)

        if normalized in self.hk_watchlist:
            return self.hk_watchlist[normalized]["name"]
        elif symbol in self.us_watchlist:
            return self.us_watchlist[symbol]["name"]
        return ""

    async def analyze_reversal_signals(self, symbol, current_price, ind, quote):
        """分析逆势买入信号（原有策略）"""
        score = 0
        reasons = []
        signal_type = "REVERSAL"

        # RSI分析 (0-30分)
        if ind['rsi'] < 20:
            score += 30
            reasons.append(f"RSI极度超卖({ind['rsi']:.1f})")
        elif ind['rsi'] < self.rsi_oversold:
            score += 25
            reasons.append(f"RSI超卖({ind['rsi']:.1f})")
        elif ind['rsi'] < 40:
            score += 15
            reasons.append(f"RSI偏低({ind['rsi']:.1f})")
        elif 40 <= ind['rsi'] <= 50:
            score += 5

        # 布林带分析 (0-25分)
        bb_range = ind['bb_upper'] - ind['bb_lower']
        bb_position_pct = (current_price - ind['bb_lower']) / bb_range * 100 if bb_range > 0 else 50

        if current_price <= ind['bb_lower']:
            score += 25
            reasons.append(f"触及布林带下轨")
        elif current_price <= ind['bb_lower'] * 1.02:
            score += 20
            reasons.append(f"接近布林带下轨")
        elif bb_position_pct < 30:
            score += 10
            reasons.append(f"布林带下半部")

        # MACD分析 (0-20分)
        if ind['macd_histogram'] > 0 and ind.get('prev_macd_histogram', 0) <= 0:
            score += 20
            reasons.append("MACD金叉")
        elif ind['macd_histogram'] > 0:
            score += 10

        # 成交量确认 (0-15分)
        volume_ratio = float(quote.volume) / float(ind['volume_sma']) if ind['volume_sma'] > 0 else 1.0
        if volume_ratio >= 1.5:
            score += 15
            reasons.append(f"成交量放大({volume_ratio:.1f}x)")
        elif volume_ratio >= 1.2:
            score += 8

        # 趋势确认 (0-10分)
        if ind['sma_20'] > ind['sma_50']:
            score += 10
            reasons.append("上升趋势")

        return {
            'type': signal_type,
            'score': score,
            'reasons': reasons,
            'strategy': 'REVERSAL'
        }

    async def analyze_breakout_signals(self, symbol, current_price, ind, quote, candles):
        """分析突破买入信号（新增策略）"""
        score = 0
        reasons = []
        signal_type = "BREAKOUT"

        try:
            if not candles or len(candles) < self.breakout_lookback:
                return {'type': signal_type, 'score': 0, 'reasons': [], 'strategy': 'BREAKOUT'}

            closes = np.array([float(c.close) for c in candles])
            highs = np.array([float(c.high) for c in candles])
            lows = np.array([float(c.low) for c in candles])
            volumes = np.array([float(c.volume) for c in candles])

            # === 1. 价格突破分析 (0-30分) ===
            # 计算近期高点
            recent_high = np.max(highs[-self.breakout_lookback:-1])  # 不包括今天
            resistance_level = recent_high * (1 - self.resistance_tolerance)

            if current_price > recent_high:
                score += 30
                reasons.append(f"突破{self.breakout_lookback}日新高(${recent_high:.2f})")
            elif current_price > resistance_level:
                score += 20
                reasons.append(f"接近突破位(${recent_high:.2f})")

            # === 2. 成交量突破分析 (0-25分) ===
            volume_ratio = float(quote.volume) / float(ind['volume_sma']) if ind['volume_sma'] > 0 else 1.0

            if volume_ratio >= self.volume_breakout_multiplier:
                score += 25
                reasons.append(f"成交量突破({volume_ratio:.1f}倍)")
            elif volume_ratio >= 1.5:
                score += 15
                reasons.append(f"成交量放大({volume_ratio:.1f}倍)")
            elif volume_ratio >= 1.2:
                score += 8

            # === 3. 动量分析 (0-20分) ===
            # 计算ROC (Rate of Change)
            if len(closes) >= self.momentum_period:
                roc = ((current_price - closes[-self.momentum_period]) / closes[-self.momentum_period]) * 100

                if roc > self.roc_threshold * 2:  # 强动量
                    score += 20
                    reasons.append(f"强势动量(ROC:{roc:.1f}%)")
                elif roc > self.roc_threshold:
                    score += 12
                    reasons.append(f"正面动量(ROC:{roc:.1f}%)")
                elif roc > 0:
                    score += 5

            # === 4. 通道突破分析 (0-15分) ===
            # 唐奇安通道
            if len(highs) >= self.donchian_period:
                upper_channel = np.max(highs[-self.donchian_period:])
                lower_channel = np.min(lows[-self.donchian_period:])
                channel_position = (current_price - lower_channel) / (upper_channel - lower_channel)

                if channel_position >= 0.95:
                    score += 15
                    reasons.append("突破唐奇安通道上轨")
                elif channel_position >= 0.8:
                    score += 8
                    reasons.append("接近通道上轨")

            # === 5. 趋势强度分析 (0-10分) ===
            # ADX可以判断趋势强度（这里用简化版）
            if ind['sma_20'] > ind['sma_50'] and ind['sma_50'] > ind.get('sma_200', ind['sma_50']):
                score += 10
                reasons.append("强势上升趋势")
            elif ind['sma_20'] > ind['sma_50']:
                score += 5
                reasons.append("上升趋势")

            # === 6. RSI动量确认（加分项）===
            # 突破策略中，RSI在50-70之间是好的
            if 50 < ind['rsi'] < 70:
                score += 5
                reasons.append(f"RSI健康({ind['rsi']:.0f})")
            elif ind['rsi'] >= 70:
                # RSI过高要减分（可能超买）
                score -= 10
                if score < 0:
                    score = 0

            logger.debug(f"   突破分析 {symbol}: 总分={score}, 原因={reasons}")

        except Exception as e:
            logger.error(f"突破信号分析失败 {symbol}: {e}")
            return {'type': signal_type, 'score': 0, 'reasons': [], 'strategy': 'BREAKOUT'}

        return {
            'type': signal_type,
            'score': score,
            'reasons': reasons,
            'strategy': 'BREAKOUT'
        }

    async def analyze_combined_signals(self, symbol, current_price, quote):
        """综合分析买入信号（结合逆势和突破）"""
        try:
            # 获取历史数据
            end_date = datetime.now()
            start_date = end_date - timedelta(days=max(60, self.breakout_lookback + 10))

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
            ind['macd_line'] = macd_result['macd'][-1]
            ind['macd_signal'] = macd_result['signal'][-1]
            ind['macd_histogram'] = macd_result['histogram'][-1]
            ind['prev_macd_histogram'] = macd_result['histogram'][-2] if len(macd_result['histogram']) > 1 else 0

            # 均线
            ind['sma_20'] = np.mean(closes[-20:]) if len(closes) >= 20 else 0
            ind['sma_50'] = np.mean(closes[-50:]) if len(closes) >= 50 else 0
            ind['volume_sma'] = np.mean(volumes[-20:]) if len(volumes) >= 20 else 0

            # ATR用于止损计算
            atr = TechnicalIndicators.atr(highs, lows, closes, period=self.atr_period)
            ind['atr'] = atr[-1] if len(atr) > 0 else 0

            # 分析两种策略信号
            reversal_signal = await self.analyze_reversal_signals(symbol, current_price, ind, quote)
            breakout_signal = await self.analyze_breakout_signals(symbol, current_price, ind, quote, candles)

            # 选择最佳信号
            best_signal = None

            if self.strategy_mode == "REVERSAL":
                best_signal = reversal_signal
            elif self.strategy_mode == "BREAKOUT":
                best_signal = breakout_signal
            elif self.strategy_mode == "HYBRID":
                # 混合模式：选择得分更高的信号
                if reversal_signal['score'] >= breakout_signal['score']:
                    best_signal = reversal_signal
                else:
                    best_signal = breakout_signal

                # 如果两种信号都较强，额外加分
                if reversal_signal['score'] > 30 and breakout_signal['score'] > 30:
                    best_signal['score'] += 10
                    best_signal['reasons'].append("双重信号确认")

            # 添加其他信息
            if best_signal and best_signal['score'] > 0:
                best_signal['symbol'] = symbol
                best_signal['price'] = current_price
                best_signal['rsi'] = ind['rsi']
                best_signal['atr'] = ind['atr']

                # 根据策略类型设置止损止盈
                if best_signal['strategy'] == 'REVERSAL':
                    best_signal['stop_loss'] = current_price - ind['atr'] * self.reversal_stop_multiplier
                    best_signal['take_profit'] = current_price + ind['atr'] * self.reversal_profit_multiplier
                else:  # BREAKOUT
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

    async def run(self):
        """主运行循环"""
        logger.info("\n" + "=" * 60)
        logger.info("🚀 启动增强版交易策略")
        logger.info("=" * 60)

        # 初始化
        self.quote_client = QuoteDataClient(self.settings)
        self.trade_client = LongportTradingClient(self.settings)

        # 初始化Slack通知
        if self.enable_slack:
            try:
                # 从配置获取webhook URL
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

        while True:
            try:
                # 检查账户状态
                account = await self.check_account_status()

                # 获取所有标的的实时行情
                quotes = await self.quote_client.get_realtime_quote(symbols)

                # 并发分析所有标的
                logger.info(f"\n🔍 分析 {len(quotes)} 个标的...")

                all_signals = []
                for quote in quotes:
                    if float(quote.last_done) <= 0:
                        continue

                    signal = await self.analyze_combined_signals(
                        quote.symbol,
                        float(quote.last_done),
                        quote
                    )

                    if signal:
                        all_signals.append(signal)

                        name = self._get_symbol_name(quote.symbol)
                        logger.info(
                            f"   📊 {quote.symbol}({name}): "
                            f"{signal['strength']} {signal['strategy']} 信号 "
                            f"(评分:{signal['score']})"
                        )

                # 按评分排序
                all_signals.sort(key=lambda x: x['score'], reverse=True)

                # 执行信号（优先处理高分信号）
                executed_count = 0
                for signal in all_signals:
                    if account['position_count'] >= self.max_positions:
                        logger.warning("⚠️ 已达最大持仓数，停止开新仓")
                        break

                    if executed_count >= 3:  # 每轮最多执行3个信号
                        break

                    success = await self.execute_signal(signal, account)
                    if success:
                        executed_count += 1
                        account['position_count'] += 1

                if executed_count == 0 and all_signals:
                    logger.info(f"   ℹ️ 有{len(all_signals)}个信号但未执行（资金/仓位限制）")
                elif executed_count > 0:
                    logger.success(f"   ✅ 本轮执行了{executed_count}个交易信号")

                # 检查现有持仓的止损止盈
                await self.check_exit_signals(quotes, account)

                # 等待下一轮
                await asyncio.sleep(60)

            except Exception as e:
                logger.error(f"运行错误: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(60)

    async def check_account_status(self):
        """检查账户状态（支持融资账户）"""
        balances = await self.trade_client.account_balance()
        positions_resp = await self.trade_client.stock_positions()

        buy_power = {}
        total_cash = {}
        net_assets = 0

        # 获取所有货币的购买力
        for balance in balances:
            currency = balance.currency
            # 购买力已经包含了融资额度
            buy_power[currency] = float(balance.buy_power) if hasattr(balance, 'buy_power') else 0
            # 总现金可能是负数（使用了融资）
            total_cash[currency] = float(balance.total_cash) if hasattr(balance, 'total_cash') else 0

            if hasattr(balance, 'net_assets'):
                net_assets = max(net_assets, float(balance.net_assets))

            # 打印调试信息
            logger.debug(f"   {currency}:")
            logger.debug(f"      购买力: ${buy_power[currency]:,.2f}")
            logger.debug(f"      总现金: ${total_cash[currency]:,.2f}")
            if hasattr(balance, 'max_finance_amount'):
                logger.debug(f"      最大融资: ${float(balance.max_finance_amount):,.2f}")
            if hasattr(balance, 'remaining_finance_amount'):
                logger.debug(f"      剩余融资: ${float(balance.remaining_finance_amount):,.2f}")

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
            "buy_power": buy_power,  # 购买力（包含融资）
            "cash": total_cash,       # 实际现金（可能为负）
            "positions": positions,
            "position_count": len(positions),
            "net_assets": net_assets
        }

    async def execute_signal(self, signal, account):
        """执行交易信号"""
        symbol = signal['symbol']

        # 检查是否已持有
        if symbol in account['positions']:
            logger.debug(f"   {symbol}: 已持有，跳过")
            return False

        # 确定交易货币
        if symbol.endswith('.HK'):
            currency = "HKD"
        elif symbol.endswith('.US'):
            currency = "USD"
        else:
            currency = "HKD"  # 默认港币

        # 获取购买力（而非现金，因为支持融资）
        available_power = account['buy_power'].get(currency, 0)

        # 如果美股没有美元购买力，尝试使用港币（长桥支持用港币买美股）
        use_hkd_for_usd = False
        if currency == "USD" and available_power < 1000:
            # 汇率大约 7.8 HKD = 1 USD
            hkd_power = account['buy_power'].get("HKD", 0)
            if hkd_power > 0:
                available_power = hkd_power / 7.8  # 转换为等值美元
                use_hkd_for_usd = True
                logger.debug(f"   {symbol}: 使用HKD购买力 ${hkd_power:.0f} (约${available_power:.0f} USD)")

        # 检查最小资金要求
        min_amount = 1000 if currency == "USD" else 10000  # 美股1000美元，港股10000港币
        if available_power < min_amount:
            logger.warning(f"   {symbol}: 购买力不足 (可用: ${available_power:.0f} {currency})")
            return False

        # 根据信号强度决定仓位大小
        if signal['strength'] == 'STRONG':
            position_size = available_power * 0.15
        elif signal['strength'] == 'NORMAL':
            position_size = available_power * 0.10
        else:  # WEAK
            position_size = available_power * 0.08

        position_size = min(position_size, available_power * self.max_position_size_pct)

        # 对于美股，如果使用港币购买，需要将价格转换
        price_for_calculation = signal['price']
        if use_hkd_for_usd:
            # 使用港币购买美股，价格需要转换为港币
            price_for_calculation = signal['price'] * 7.8

        quantity = int(position_size / price_for_calculation)

        # 简化处理：调整到最小交易单位
        # 港股通常100股一手，美股1股起
        if symbol.endswith('.HK'):
            lot_size = 100
            quantity = (quantity // lot_size) * lot_size
        # 美股最小1股
        quantity = max(1, int(quantity))

        if quantity <= 0:
            logger.warning(f"   {symbol}: 计算数量为0")
            return False

        try:
            # 下单
            logger.info(f"\n📈 执行{signal['strategy']}买入信号:")
            logger.info(f"   标的: {symbol} ({self._get_symbol_name(symbol)})")
            logger.info(f"   价格: ${signal['price']:.2f}")
            logger.info(f"   数量: {quantity}")
            logger.info(f"   策略: {signal['strategy']}")
            logger.info(f"   原因: {', '.join(signal['reasons'])}")
            logger.info(f"   止损: ${signal['stop_loss']:.2f}")
            logger.info(f"   止盈: ${signal['take_profit']:.2f}")

            # 真实下单
            if self.enable_trading:
                order_request = {
                    "symbol": symbol,
                    "side": "BUY",
                    "quantity": quantity,
                    "price": signal['price'],
                }

                order_response = await self.trade_client.submit_order(order_request)
                order_id = order_response.get("order_id")
                logger.success(f"   ✅ 订单提交成功 (ID: {order_id})")

                # 记录订单到数据库
                await self.order_manager.save_order(
                    order_id=order_id,
                    symbol=symbol,
                    side="BUY",
                    quantity=quantity,
                    price=signal['price'],
                    status="New"  # 初始状态
                )
            else:
                logger.info("   ⚠️ 模拟模式，不执行真实下单")

            # 记录止损止盈
            self.positions_with_stops[symbol] = {
                "entry_price": signal['price'],
                "stop_loss": signal['stop_loss'],
                "take_profit": signal['take_profit'],
                "strategy": signal['strategy'],
                "entry_time": datetime.now()
            }

            # 发送Slack通知
            if self.slack:
                message = (
                    f"*{signal['strategy']}买入信号执行*\n"
                    f"• 标的: {symbol} ({self._get_symbol_name(symbol)})\n"
                    f"• 价格: ${signal['price']:.2f}\n"
                    f"• 数量: {quantity}股\n"
                    f"• 评分: {signal['score']}\n"
                    f"• 原因: {', '.join(signal['reasons'][:3])}\n"
                    f"• 止损: ${signal['stop_loss']:.2f} (-{(1-signal['stop_loss']/signal['price'])*100:.1f}%)\n"
                    f"• 止盈: ${signal['take_profit']:.2f} (+{(signal['take_profit']/signal['price']-1)*100:.1f}%)"
                )
                await self.slack.send(message)

            return True

        except Exception as e:
            logger.error(f"   ❌ 下单失败: {e}")
            if self.slack:
                await self.slack.send(f"⚠️ 下单失败: {symbol} - {str(e)}")
            return False

    async def execute_sell(self, symbol, current_price, position, reason):
        """执行卖出操作"""
        try:
            quantity = position['quantity']

            logger.info(f"\n📉 执行{reason}卖出:")
            logger.info(f"   标的: {symbol} ({self._get_symbol_name(symbol)})")
            logger.info(f"   价格: ${current_price:.2f}")
            logger.info(f"   数量: {quantity}")
            logger.info(f"   原因: {reason}")

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

                # 记录订单
                await self.order_manager.save_order(
                    order_id=order_id,
                    symbol=symbol,
                    side="SELL",
                    quantity=quantity,
                    price=current_price,
                    status="New"
                )
            else:
                logger.info("   ⚠️ 模拟模式，不执行真实卖出")

            # 发送Slack通知
            if self.slack:
                entry_price = self.positions_with_stops[symbol]['entry_price']
                pnl = (current_price - entry_price) * quantity
                pnl_pct = (current_price / entry_price - 1) * 100

                emoji = "🛑" if reason == "止损" else "🎉"
                message = (
                    f"{emoji} *{reason}卖出执行*\n"
                    f"• 标的: {symbol} ({self._get_symbol_name(symbol)})\n"
                    f"• 卖出价: ${current_price:.2f}\n"
                    f"• 数量: {quantity}股\n"
                    f"• 盈亏: ${pnl:.2f} ({pnl_pct:+.2f}%)"
                )
                await self.slack.send(message)

            # 清理记录
            if symbol in self.positions_with_stops:
                del self.positions_with_stops[symbol]

            return True

        except Exception as e:
            logger.error(f"   ❌ 卖出失败: {e}")
            if self.slack:
                await self.slack.send(f"⚠️ 卖出失败: {symbol} - {str(e)}")
            return False

    async def check_exit_signals(self, quotes, account):
        """检查止损止盈信号"""
        for quote in quotes:
            symbol = quote.symbol
            if symbol not in account['positions']:
                continue

            current_price = float(quote.last_done)
            if current_price <= 0:
                continue

            position = account['positions'][symbol]
            entry_price = position['cost']

            if symbol not in self.positions_with_stops:
                continue

            stops = self.positions_with_stops[symbol]
            stop_loss = stops['stop_loss']
            take_profit = stops['take_profit']

            pnl_pct = (current_price / entry_price - 1) * 100

            # 检查止损
            if current_price <= stop_loss:
                logger.warning(f"\n🛑 {symbol} 触及止损!")
                logger.warning(f"   入场价: ${entry_price:.2f}")
                logger.warning(f"   当前价: ${current_price:.2f}")
                logger.warning(f"   止损位: ${stop_loss:.2f}")
                logger.warning(f"   盈亏: {pnl_pct:.2f}%")
                # 执行卖出
                await self.execute_sell(symbol, current_price, position, "止损")

            # 检查止盈
            elif current_price >= take_profit:
                logger.success(f"\n🎉 {symbol} 触及止盈!")
                logger.success(f"   入场价: ${entry_price:.2f}")
                logger.success(f"   当前价: ${current_price:.2f}")
                logger.success(f"   止盈位: ${take_profit:.2f}")
                logger.success(f"   盈亏: {pnl_pct:.2f}%")
                # 执行卖出
                await self.execute_sell(symbol, current_price, position, "止盈")


async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='增强版交易策略')
    parser.add_argument('--builtin', action='store_true', help='使用内置监控列表')
    parser.add_argument('--mode', choices=['REVERSAL', 'BREAKOUT', 'HYBRID'],
                       default='HYBRID', help='策略模式')
    parser.add_argument('--dry-run', action='store_true', help='模拟运行，不执行真实交易')
    parser.add_argument('--no-slack', action='store_true', help='禁用Slack通知')
    args = parser.parse_args()

    # 创建策略实例
    strategy = EnhancedTradingStrategy(
        use_builtin_watchlist=args.builtin,
        enable_trading=not args.dry_run,  # dry-run模式下不真实交易
        enable_slack=not args.no_slack    # 除非指定no-slack，否则启用
    )
    strategy.strategy_mode = args.mode

    # 显示运行模式
    logger.info(f"运行模式: {'模拟' if args.dry_run else '实盘'}")
    logger.info(f"Slack通知: {'禁用' if args.no_slack else '启用'}")

    await strategy.run()


if __name__ == "__main__":
    asyncio.run(main())
