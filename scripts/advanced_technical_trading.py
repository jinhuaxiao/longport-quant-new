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
from longport_quant.utils import LotSizeHelper
from longport_quant.persistence.order_manager import OrderManager
from longport_quant.persistence.stop_manager import StopLossManager


def sanitize_unicode(text: str) -> str:
    """清理无效的Unicode字符,防止surrogate pair错误

    Args:
        text: 需要清理的字符串

    Returns:
        清理后的字符串
    """
    if not text:
        return text

    try:
        # 使用'surrogateescape'错误处理器编码再解码,去除无效字符
        # 或者使用'ignore'直接忽略无效字符
        return text.encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')
    except Exception:
        # 如果还是失败,返回ASCII安全的版本
        return text.encode('ascii', errors='ignore').decode('ascii')


class AdvancedTechnicalTrader:
    """高级技术指标交易系统"""

    def __init__(self, use_builtin_watchlist=False, max_iterations=None):
        """初始化交易系统

        Args:
            use_builtin_watchlist: 是否使用内置的监控列表（而不是从watchlist.yml加载）
            max_iterations: 最大迭代次数，None表示无限循环
        """
        self.settings = get_settings()
        self.beijing_tz = ZoneInfo('Asia/Shanghai')
        self.slack = None  # Will be initialized in run()
        self.use_builtin_watchlist = use_builtin_watchlist
        self.max_iterations = max_iterations

        # 港股监控列表（用户自定义15只股票）
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
            "9660.HK": {"name": "地平线机器人", "sector": "半导体"},
            "2382.HK": {"name": "舜宇光学科技", "sector": "半导体"},

            # 新能源汽车
            "1211.HK": {"name": "比亚迪", "sector": "汽车"},
            "3750.HK": {"name": "宁德时代", "sector": "新能源"},

            # 消费股
            "9992.HK": {"name": "泡泡玛特", "sector": "消费"},
            "1929.HK": {"name": "周大福", "sector": "消费"},

            # 工业股
            "0558.HK": {"name": "力劲科技", "sector": "工业"},

            # 银行股
            "0005.HK": {"name": "汇丰控股", "sector": "银行"},
            "1398.HK": {"name": "工商银行", "sector": "银行"},

            # 能源股
            "0857.HK": {"name": "中国石油", "sector": "能源"},
            "0883.HK": {"name": "中国海洋石油", "sector": "能源"},
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

        # 交易参数（动态调整）
        self.max_positions = 999  # 不限制持仓数量（实际受资金限制）

        # 分市场持仓限制（避免单一市场过度集中）
        self.max_positions_by_market = {
            'HK': 8,   # 港股最多8个
            'US': 5,   # 美股最多5个
            'SH': 2,   # A股上交所最多2个
            'SZ': 2,   # A股深交所最多2个
        }

        self.min_position_size_pct = 0.05  # 最小仓位比例（账户总值的5%）
        self.max_position_size_pct = 0.30  # 最大仓位比例（账户总值的30%）
        self.max_daily_trades_per_symbol = 2  # 每个标的每天最多交易次数（可根据VIP级别调整）

        # 动态风控参数
        self.use_adaptive_budget = True  # 启用自适应预算
        self.min_cash_reserve = 1000  # 最低现金储备（紧急备用金）

        # 订单数据库管理器
        self.order_manager = OrderManager()

        # 临时缓存（用于快速检查，定期与数据库同步）
        self.executed_today = {}  # {symbol: trade_count} 今日交易次数
        self.pending_orders = {}  # {symbol: {order_id, timestamp, side, quantity}}（仅缓存）
        self.order_cache_timeout = 300  # 订单缓存5分钟超时

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

        # 止损止盈持久化管理器
        self.stop_manager = StopLossManager()

        # 账户信息缓存
        self._cached_account = None
        self._last_account_update = None

        # 手数辅助工具
        self.lot_size_helper = LotSizeHelper()

        logger.info("初始化高级技术指标交易系统")
        logger.info(f"策略: RSI + 布林带 + MACD + 成交量确认 + ATR动态止损")

    def _normalize_hk_symbol(self, symbol):
        """标准化港股代码格式 - 确保是4位数字"""
        if symbol.endswith('.HK'):
            code = symbol[:-3]  # 移除.HK后缀
            # 如果代码少于4位，在前面补0
            if len(code) < 4 and code.isdigit():
                code = code.zfill(4)  # 补齐到4位
                return f"{code}.HK"
        return symbol

    def _get_market(self, symbol):
        """获取标的所属市场

        Returns:
            str: 市场代码 ('HK', 'US', 'SH', 'SZ', 'UNKNOWN')
        """
        if '.HK' in symbol:
            return 'HK'
        elif '.US' in symbol:
            return 'US'
        elif '.SH' in symbol:
            return 'SH'
        elif '.SZ' in symbol:
            return 'SZ'
        return 'UNKNOWN'

    def _get_symbol_name(self, symbol):
        """获取标的的中文名称"""
        # 先尝试标准化港股代码
        normalized_symbol = self._normalize_hk_symbol(symbol)

        name = ""
        # 检查港股（使用标准化后的代码）
        if normalized_symbol in self.hk_watchlist:
            name = self.hk_watchlist[normalized_symbol]["name"]
        elif symbol in self.hk_watchlist:  # 也尝试原始代码
            name = self.hk_watchlist[symbol]["name"]
        # 检查美股
        elif symbol in self.us_watchlist:
            name = self.us_watchlist[symbol]["name"]
        # 检查A股
        elif hasattr(self, 'a_watchlist') and symbol in self.a_watchlist:
            name = self.a_watchlist[symbol]["name"]

        # 清理Unicode字符以防止编码错误
        return sanitize_unicode(name) if name else ""

    async def run(self):
        """主运行循环"""
        logger.info("=" * 70)
        logger.info("启动高级技术指标自动交易系统")
        logger.info(f"策略组合: RSI({self.rsi_period}) + BB({self.bb_period},{self.bb_std}σ) + MACD + Volume + ATR")
        logger.info("=" * 70)

        # 初始化实时信号队列
        self.signal_queue = asyncio.Queue()
        self.realtime_quotes = {}  # 存储最新行情
        self.websocket_enabled = False  # WebSocket订阅标志

        # 初始化客户端
        async with QuoteDataClient(self.settings) as quote_client, \
                   LongportTradingClient(self.settings) as trade_client, \
                   SlackNotifier(self.settings.slack_webhook_url) as slack:

            self.quote_client = quote_client
            self.trade_client = trade_client
            self.slack = slack

            # 保存主事件循环引用（供WebSocket回调使用）
            self._main_loop = asyncio.get_event_loop()

            # 加载监控列表
            if self.use_builtin_watchlist:
                # 使用内置监控列表
                symbols = list(self.hk_watchlist.keys()) + list(self.us_watchlist.keys())
                logger.info(f"✅ 使用内置监控列表")
                logger.info(f"   港股: {len(self.hk_watchlist)} 个标的")
                logger.info(f"   美股: {len(self.us_watchlist)} 个标的")
                logger.info(f"   总计: {len(symbols)} 个标的")

                # 尝试设置WebSocket实时订阅
                await self.setup_realtime_subscription(symbols)
            else:
                # 从watchlist.yml加载
                watchlist = WatchlistLoader().load()
                symbols = list(watchlist.symbols())
                logger.info(f"✅ 从配置文件加载监控列表: {len(symbols)} 个标的")

            # 检查账户状态
            account = await self.check_account_status()
            self._display_account_info(account)

            # 初始化时检查今日已有的订单
            await self._init_today_orders()

            # 从数据库加载已保存的止损止盈设置
            logger.info("📂 加载持久化的止损止盈设置...")
            all_stops = await self.stop_manager.load_active_stops()

            # 过滤：只保留实际持仓中存在的止损记录（排除测试数据）
            current_positions = set(account.get("positions", {}).keys())
            self.positions_with_stops = {
                symbol: stops
                for symbol, stops in all_stops.items()
                if symbol in current_positions
            }

            # 如果有被过滤掉的记录，显示警告
            filtered_out = set(all_stops.keys()) - current_positions
            if filtered_out:
                logger.warning(
                    f"⚠️  过滤掉 {len(filtered_out)} 个不在持仓中的止损记录: "
                    f"{list(filtered_out)}"
                )

            if self.positions_with_stops:
                logger.info(f"✅ 已加载 {len(self.positions_with_stops)} 个有效止损止盈设置:")
                for symbol, stops in self.positions_with_stops.items():
                    logger.info(f"  {symbol}: 止损=${stops['stop_loss']:.2f}, 止盈=${stops['take_profit']:.2f}")
            else:
                logger.info("📭 没有找到有效的止损止盈设置")

            # 启动信号处理器（不论是否使用WebSocket都需要处理信号队列）
            logger.info("🚀 准备启动信号处理器...")
            processor_task = asyncio.create_task(self.signal_processor())
            logger.success(f"✅ 信号处理器任务已创建: {processor_task}")

            # 等待一小段时间，确保处理器已启动
            await asyncio.sleep(0.5)

            # 检查处理器是否正常运行
            if processor_task.done():
                try:
                    processor_task.result()
                except Exception as e:
                    logger.error(f"❌ 信号处理器启动失败: {e}")
                    import traceback
                    traceback.print_exc()
                    raise
            else:
                logger.success("✅ 信号处理器正在运行")

            # 主循环
            iteration = 0
            while True:
                iteration += 1
                logger.info(f"\n{'='*70}")
                logger.info(f"第 {iteration} 轮扫描 - {datetime.now(self.beijing_tz).strftime('%H:%M:%S')}")
                logger.info(f"{'='*70}")

                # 检查是否达到最大迭代次数
                if self.max_iterations and iteration > self.max_iterations:
                    logger.info(f"✅ 达到最大迭代次数 {self.max_iterations}，停止运行")
                    break

                # 定期重置数据库连接池（防止连接泄漏）
                if iteration % 5 == 0:  # 每5轮（5分钟）重置一次
                    try:
                        await self.stop_manager.reset_pool()

                        # 检查文件描述符数量并执行自动降级
                        import os
                        pid = os.getpid()
                        try:
                            fd_count = len(os.listdir(f'/proc/{pid}/fd'))
                            logger.info(f"📊 当前文件描述符数量: {fd_count}")

                            # 🔴 危险级别：超过 900，自动退出重启
                            if fd_count > 900:
                                logger.critical(f"🔴 文件描述符危险 ({fd_count}/1024)！强制退出以防止系统崩溃")
                                logger.critical("   建议使用 bash scripts/safe_restart_trading.sh 重启")
                                break  # 退出主循环

                            # 🟠 严重级别：超过 800，暂停交易
                            elif fd_count > 800:
                                logger.error(f"🟠 文件描述符过多 ({fd_count})！暂停交易，仅保留监控")
                                # 禁用WebSocket（如果启用）
                                if hasattr(self, 'websocket_enabled') and self.websocket_enabled:
                                    logger.warning("   🔌 禁用 WebSocket 订阅以减少连接")
                                    try:
                                        await self.quote_client.unsubscribe(
                                            list(self.subscribed_symbols),
                                            [openapi.SubType.Quote]
                                        )
                                        self.websocket_enabled = False
                                    except Exception as e:
                                        logger.debug(f"取消订阅失败: {e}")

                            # 🟡 警告级别：超过 600，禁用 WebSocket
                            elif fd_count > 600:
                                logger.warning(f"🟡 文件描述符较多 ({fd_count})，禁用 WebSocket")
                                if hasattr(self, 'websocket_enabled') and self.websocket_enabled:
                                    try:
                                        await self.quote_client.unsubscribe(
                                            list(self.subscribed_symbols),
                                            [openapi.SubType.Quote]
                                        )
                                        self.websocket_enabled = False
                                        logger.info("   ✅ 已切换到轮询模式")
                                    except Exception as e:
                                        logger.debug(f"取消订阅失败: {e}")

                            # 🟢 正常级别：超过 300，触发紧急重置
                            elif fd_count > 300:
                                logger.info(f"🟢 文件描述符正常 ({fd_count})，执行紧急连接池重置")
                                await self.stop_manager.reset_pool()

                        except Exception as fd_check_error:
                            logger.debug(f"文件描述符检查失败: {fd_check_error}")

                    except Exception as e:
                        logger.warning(f"重置连接池失败: {e}")

                try:
                    # 1. 检查当前活跃市场
                    active_markets, us_session = self.get_active_markets()
                    if not active_markets:
                        logger.info("⏰ 当前时间: 不在交易时段")
                        await asyncio.sleep(60)
                        continue

                    # 1a. 动态合并监控列表（确保包含所有持仓）
                    # 获取当前账户持仓
                    temp_account = await self.check_account_status()
                    raw_position_symbols = list(temp_account.get("positions", {}).keys())

                    # 标准化持仓中的港股代码
                    position_symbols = []
                    for sym in raw_position_symbols:
                        normalized = self._normalize_hk_symbol(sym)
                        position_symbols.append(normalized)
                        if normalized != sym:
                            logger.debug(f"  标准化股票代码: {sym} → {normalized}")

                    # 合并原始监控列表和持仓列表（去重）
                    all_monitored_symbols = list(set(symbols + position_symbols))

                    # 如果有新的持仓股票，显示信息
                    new_positions = [s for s in position_symbols if s not in symbols]
                    if new_positions:
                        logger.info(f"📦 检测到持仓股票不在监控列表，自动加入: {new_positions}")
                        logger.info(f"   原始监控: {len(symbols)} 个")
                        logger.info(f"   持仓股票: {len(position_symbols)} 个")
                        logger.info(f"   合并后: {len(all_monitored_symbols)} 个")

                        # 动态更新WebSocket订阅（如果启用）
                        await self.update_subscription_for_positions(position_symbols)

                    # 2. 根据活跃市场过滤标的（使用合并后的列表）
                    active_symbols = self.filter_symbols_by_market(all_monitored_symbols, active_markets)
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

                    # 3a. 处理待买入队列（上轮清理持仓后的买入）
                    if hasattr(self, 'pending_buy_queue') and self.pending_buy_queue:
                        logger.info(f"📋 处理待买入队列: {len(self.pending_buy_queue)}个标的")
                        for symbol, buy_info in list(self.pending_buy_queue.items()):
                            # 检查是否超时（超过5分钟移除）
                            if datetime.now() - buy_info['added_time'] > timedelta(minutes=5):
                                del self.pending_buy_queue[symbol]
                                logger.info(f"  ⏰ {symbol}: 待买入超时，移除队列")
                                continue

                            # 重新获取当前价格
                            current_quote = None
                            for q in quotes if 'quotes' in locals() else []:
                                if q.symbol == symbol:
                                    current_quote = q
                                    break

                            if current_quote:
                                current_price = float(current_quote.last_done)
                                signal = buy_info['signal']

                                # 检查是否可以买入
                                can_buy = await self._can_open_position(symbol, account)
                                if can_buy:
                                    logger.info(f"  📈 {symbol}: 执行延迟买入（资金已到账）")
                                    await self.execute_signal(symbol, signal, current_price, account)
                                    del self.pending_buy_queue[symbol]
                                else:
                                    logger.info(f"  ⏳ {symbol}: 资金未到账或条件不满足，继续等待")

                    # 4. 定期刷新今日订单（每10轮刷新一次）
                    if iteration % 10 == 1:
                        logger.info("🔄 刷新今日订单缓存...")
                        await self._refresh_today_orders()

                    # 4b. 定期清理旧订单（每100轮清理一次，保留7天）
                    if iteration % 100 == 1:
                        logger.debug("🗑️ 清理7天前的订单记录...")
                        await self.order_manager.cleanup_old_orders(days=7)

                    # 5. 更新待成交订单状态
                    for symbol in list(self.pending_orders.keys()):
                        await self._update_order_status(symbol)

                    # 5. 检查现有持仓的止损止盈
                    await self.check_exit_signals(quotes, account)

                    # 6. 并发分析所有标的（大幅提升效率）
                    logger.info(f"🚀 开始并发分析 {len(quotes)} 个标的...")

                    # 使用并发分析替代串行循环
                    all_signals = await self.concurrent_analysis(quotes, account)

                    # 按评分排序，优先处理高质量信号
                    if all_signals:
                        sorted_signals = sorted(all_signals,
                                              key=lambda x: x.get('strength', 0),
                                              reverse=True)

                        logger.info(f"📊 生成 {len(sorted_signals)} 个信号，按评分排序处理")

                        # 处理排序后的信号
                        for signal_data in sorted_signals:
                            symbol = signal_data['symbol']
                            signal = signal_data['signal']
                            current_price = signal_data['price']
                            quote = signal_data['quote']

                            # 显示信号
                            await self._display_signal(symbol, signal, current_price)

                            # 如果是WebSocket模式且信号处理器正在运行，将信号加入队列
                            if self.websocket_enabled and hasattr(self, 'signal_queue'):
                                # 计算优先级（负数，因为PriorityQueue是最小堆）
                                priority = -signal.get('strength', 0)

                                # 加入优先级队列
                                await self.signal_queue.put((
                                    priority,
                                    {
                                        'symbol': symbol,
                                        'signal': signal,
                                        'price': current_price,
                                        'quote': quote,
                                        'timestamp': datetime.now()
                                    }
                                ))

                                logger.info(f"🔔 {symbol}: 轮询信号入队（WebSocket模式），评分={signal.get('strength', 0)}")
                                continue  # 交给信号处理器处理，避免重复

                            # 非WebSocket模式，直接处理
                            # 检查是否可以开仓
                            can_open = await self._can_open_position(symbol, account)

                            # 如果不能开仓（满仓），尝试智能清理腾出空间
                            if not can_open:
                                logger.info(f"  💼 {symbol}: 检测到满仓（{account['position_count']}/{self.max_positions}），尝试智能仓位管理")
                                logger.debug(f"     新信号: {signal['type']}, 评分: {signal['strength']}/100")

                                made_room = await self._try_make_room(signal, account)
                                if made_room:
                                    # 标记为待买入，下一轮再处理
                                    if not hasattr(self, 'pending_buy_queue'):
                                        self.pending_buy_queue = {}
                                    self.pending_buy_queue[symbol] = {
                                        'signal': signal,
                                        'added_time': datetime.now()
                                    }
                                    logger.success(f"  ✅ {symbol}: 已成功执行仓位清理，加入待买入队列（等待资金到账后执行）")
                                    # 不立即买入，等待资金到账
                                    can_open = False
                                else:
                                    logger.info(f"  ⏭️  {symbol}: 评估后决定保持当前持仓，跳过新信号")

                            if can_open:
                                await self.execute_signal(symbol, signal, current_price, account)
                    else:
                        logger.info("📉 本轮未生成有效交易信号")

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

    async def concurrent_analysis(self, quotes, account):
        """
        并发分析所有股票，大幅提升效率

        优势:
        1. 并发执行，速度提升N倍（N=股票数）
        2. 同时捕捉多个交易机会
        3. 避免因串行处理错过短暂信号
        """
        import asyncio
        import time

        start_time = time.time()

        # 创建分析任务列表
        analysis_tasks = []
        task_metadata = {}  # 存储任务元数据

        for quote in quotes:
            symbol = quote.symbol
            current_price = float(quote.last_done)

            if current_price <= 0:
                continue

            # 创建分析任务
            task = asyncio.create_task(
                self._analyze_single_symbol(symbol, current_price, quote)
            )

            # 存储元数据
            task_metadata[task] = {
                'symbol': symbol,
                'price': current_price,
                'quote': quote
            }

            analysis_tasks.append(task)

        if not analysis_tasks:
            logger.info("  ⚠️ 无有效标的需要分析")
            return []

        logger.info(f"  ⚡ 并发分析 {len(analysis_tasks)} 个标的...")

        # 并发执行所有分析任务
        results = await asyncio.gather(*analysis_tasks, return_exceptions=True)

        # 收集有效信号
        valid_signals = []

        for task, result in zip(analysis_tasks, results):
            metadata = task_metadata[task]
            symbol = metadata['symbol']

            if isinstance(result, Exception):
                logger.debug(f"  ❌ {symbol}: 分析失败 - {result}")
                continue

            if result:  # 有信号生成
                # 添加元数据到信号
                signal_data = {
                    'symbol': symbol,
                    'signal': result,
                    'price': metadata['price'],
                    'quote': metadata['quote'],
                    'strength': result.get('strength', 0)
                }
                valid_signals.append(signal_data)
                logger.debug(f"  ✅ {symbol}: 生成信号，评分={result.get('strength', 0)}")

        elapsed = time.time() - start_time
        logger.info(f"  ⏱️ 并发分析完成，耗时 {elapsed:.2f}秒，生成 {len(valid_signals)} 个信号")

        return valid_signals

    async def _analyze_single_symbol(self, symbol, current_price, quote):
        """
        分析单个标的（供并发调用）
        """
        try:
            # 调用原有的分析方法
            signal = await self.analyze_symbol_advanced(symbol, current_price, quote)
            return signal
        except Exception as e:
            logger.debug(f"分析 {symbol} 失败: {e}")
            raise  # 重新抛出异常，让gather捕获

    async def setup_realtime_subscription(self, symbols):
        """
        设置WebSocket实时订阅，获取推送行情

        优势:
        1. 实时推送，延迟极低
        2. 立即响应价格变化
        3. 捕捉瞬间机会
        """
        try:
            from longport import openapi

            logger.info("\n📡 设置实时行情订阅...")

            # 订阅实时行情
            await self.quote_client.subscribe(
                symbols=symbols,
                sub_types=[openapi.SubType.Quote],  # 订阅报价数据
                is_first_push=True  # 立即推送当前数据
            )

            # 设置行情回调
            await self.quote_client.set_on_quote(self.on_realtime_quote)

            self.websocket_enabled = True
            self.subscribed_symbols = set(symbols)  # 记录已订阅的股票
            logger.success(f"✅ 成功订阅 {len(symbols)} 个标的的实时行情推送")
            logger.info("   WebSocket连接已建立，将实时接收行情更新")

            # 信号处理器已在主循环开始时启动，无需重复启动

        except Exception as e:
            logger.warning(f"⚠️ WebSocket订阅失败，将使用轮询模式: {e}")
            self.websocket_enabled = False
            self.subscribed_symbols = set()

    async def update_subscription_for_positions(self, position_symbols):
        """
        动态更新订阅，确保所有持仓都被监控

        当发现新持仓时，自动加入WebSocket订阅
        """
        if not self.websocket_enabled:
            return  # 如果WebSocket未启用，跳过

        try:
            from longport import openapi

            # 检查未订阅的持仓
            unsubscribed = []
            for symbol in position_symbols:
                if symbol not in self.subscribed_symbols:
                    unsubscribed.append(symbol)

            if unsubscribed:
                logger.info(f"📡 动态订阅新持仓股票: {unsubscribed}")

                # 订阅新的股票
                await self.quote_client.subscribe(
                    symbols=unsubscribed,
                    sub_types=[openapi.SubType.Quote],
                    is_first_push=True
                )

                # 更新已订阅列表
                self.subscribed_symbols.update(unsubscribed)
                logger.success(f"✅ 成功新增订阅 {len(unsubscribed)} 个持仓股票")

        except Exception as e:
            logger.warning(f"⚠️ 动态订阅失败: {e}")

    def on_realtime_quote(self, symbol, quote):
        """
        实时行情推送回调

        当收到新行情时立即触发分析
        """
        try:
            # 更新最新行情
            self.realtime_quotes[symbol] = quote

            # 由于回调在不同线程，需要安全地调度到主事件循环
            if hasattr(self, '_main_loop'):
                asyncio.run_coroutine_threadsafe(
                    self._handle_realtime_update(symbol, quote),
                    self._main_loop
                )

        except Exception as e:
            logger.debug(f"处理实时行情失败 {symbol}: {e}")

    async def _handle_realtime_update(self, symbol, quote):
        """
        处理实时行情更新

        优先级：
        1. 检查持仓的止损止盈（最高优先级）
        2. 分析新的买入信号
        """
        try:
            current_price = float(quote.last_done)
            if current_price <= 0:
                return

            # 步骤1：检查是否为持仓标的（优先处理止损止盈）
            if hasattr(self, '_cached_account') and self._cached_account:
                positions = self._cached_account.get("positions", {})

                if symbol in positions:
                    position = positions[symbol]

                    # 实时检查止损止盈
                    triggered, trigger_type = await self.check_realtime_stop_loss(
                        symbol, current_price, position
                    )

                    if triggered:
                        logger.info(f"⚡ {symbol}: 实时{trigger_type}已执行")
                        # 更新缓存的账户信息，移除已平仓的持仓
                        if symbol in self._cached_account["positions"]:
                            del self._cached_account["positions"][symbol]
                            self._cached_account["position_count"] -= 1
                        return  # 止损止盈后不再分析买入信号

                # 步骤2：如果不是持仓或未触发止损止盈，分析买入信号
                else:
                    signal = await self.analyze_symbol_advanced(symbol, current_price, quote)

                    if signal:
                        # 计算优先级（负数，因为PriorityQueue是最小堆）
                        priority = -signal.get('strength', 0)

                        # 加入优先级队列
                        await self.signal_queue.put((
                            priority,
                            {
                                'symbol': symbol,
                                'signal': signal,
                                'price': current_price,
                                'quote': quote,
                                'timestamp': datetime.now()
                            }
                        ))

                        logger.info(f"🔔 {symbol}: 实时买入信号入队，评分={signal.get('strength', 0)}")

        except Exception as e:
            logger.debug(f"实时处理失败 {symbol}: {e}")

    async def signal_processor(self):
        """
        信号处理器 - 按优先级处理信号队列
        """
        logger.info("🚀 启动信号处理器，按优先级处理交易信号...")

        while True:
            try:
                # 从优先级队列获取信号
                logger.debug("⏳ 等待信号队列...")
                priority, signal_data = await self.signal_queue.get()
                logger.info(f"📥 收到信号: {signal_data.get('symbol')}, 优先级={-priority}")

                symbol = signal_data['symbol']
                signal_type = signal_data.get('type', '')
                current_price = signal_data['price']

                # 处理止损止盈信号（最高优先级）
                if signal_type in ['STOP_LOSS', 'TAKE_PROFIT']:
                    position = signal_data['position']
                    reason = signal_data['reason']

                    logger.info(f"\n🚨 处理{reason}信号: {symbol}, 优先级={-priority}")

                    # 执行卖出
                    await self._execute_sell(symbol, current_price, position, reason)
                    continue

                # 处理普通交易信号
                signal = signal_data.get('signal')
                if signal:
                    logger.info(f"\n📌 处理交易信号: {symbol}, 评分={signal.get('strength', 0)}")

                    # 检查账户状态
                    account = await self.check_account_status()

                    # 显示信号
                    await self._display_signal(symbol, signal, current_price)

                    # 检查是否可以开仓
                    can_open = await self._can_open_position(symbol, account)
                    made_room = False  # 标记是否尝试清理过仓位

                    # 如果不能开仓，检查是否因为满仓
                    if not can_open and account["position_count"] >= self.max_positions:
                        logger.info(f"  💼 {symbol}: 检测到满仓（{account['position_count']}/{self.max_positions}），尝试智能仓位管理")
                        logger.debug(f"     新信号: {signal['type']}, 评分: {signal['strength']}/100")

                        # 尝试清理弱势持仓
                        made_room = await self._try_make_room(signal, account)
                        if made_room:
                            logger.success(f"  ✅ {symbol}: 已成功执行仓位清理，等待下一轮检查后执行买入")
                            # 重新将信号加入队列，等待下一轮处理（确保资金已到账）
                            priority = signal.get('strength', 50)
                            await self.signal_queue.put((
                                -priority,  # 负数表示高优先级
                                {
                                    'symbol': symbol,
                                    'signal': signal,
                                    'price': current_price,
                                    'timestamp': datetime.now()
                                }
                            ))
                        else:
                            logger.info(f"  ⏭️  {symbol}: 评估后决定保持当前持仓，跳过新信号")

                    # 执行交易
                    if can_open:
                        await self.execute_signal(symbol, signal, current_price, account)
                    elif not made_room:
                        logger.info(f"  ⏳ {symbol}: 无法开仓，跳过")

            except asyncio.QueueEmpty:
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"❌ 信号处理器错误: {type(e).__name__}: {e}")
                import traceback
                logger.error(f"   错误详情:\n{traceback.format_exc()}")
                await asyncio.sleep(1)
                # 继续运行，不要让处理器崩溃

    async def check_account_status(self, use_cache=False):
        """检查账户状态（支持融资账户和缓存）

        Args:
            use_cache: 是否使用缓存（实时行情处理时使用）
        """
        try:
            # 如果要求使用缓存且缓存有效（5秒内），返回缓存
            if use_cache and self._cached_account and self._last_account_update:
                cache_age = (datetime.now() - self._last_account_update).total_seconds()
                if cache_age < 5:  # 缓存5秒内有效
                    return self._cached_account

            balances = await self.trade_client.account_balance()
            positions_resp = await self.trade_client.stock_positions()

            cash = {}
            buy_power = {}
            net_assets = {}

            for balance in balances:
                currency = balance.currency

                # 使用buy_power（购买力）而不是total_cash
                # buy_power已经考虑了融资额度和可用资金
                buy_power[currency] = float(balance.buy_power) if hasattr(balance, 'buy_power') else 0

                # 记录净资产
                net_assets[currency] = float(balance.net_assets) if hasattr(balance, 'net_assets') else 0

                # 获取实际可用现金（从cash_infos中提取）
                actual_cash = 0
                if hasattr(balance, 'cash_infos') and balance.cash_infos:
                    for cash_info in balance.cash_infos:
                        if cash_info.currency == currency:
                            # available_cash是实际可用现金（可能为负，表示融资）
                            actual_cash = float(cash_info.available_cash)
                            break

                # 如果是融资账户且现金为负，使用购买力
                # 否则使用实际现金和购买力的较小值（保守策略）
                if actual_cash < 0:
                    # 融资状态，使用购买力
                    cash[currency] = buy_power[currency]
                    logger.debug(f"  💳 {currency} 融资账户: 购买力=${buy_power[currency]:,.2f}, 实际现金=${actual_cash:,.2f}")
                else:
                    # 现金充足，使用实际现金
                    cash[currency] = actual_cash
                    logger.debug(f"  💰 {currency} 现金账户: 可用现金=${actual_cash:,.2f}")

            positions = {}
            for channel in positions_resp.channels:
                for pos in channel.positions:
                    # 标准化港股代码
                    symbol = self._normalize_hk_symbol(pos.symbol)
                    positions[symbol] = {
                        "quantity": pos.quantity,
                        "available_quantity": pos.available_quantity,
                        "cost": float(pos.cost_price) if pos.cost_price else 0,
                        "currency": pos.currency,
                        "market": pos.market
                    }

            account_data = {
                "cash": cash,
                "buy_power": buy_power,
                "net_assets": net_assets,
                "positions": positions,
                "position_count": len(positions)
            }

            # 更新缓存
            self._cached_account = account_data
            self._last_account_update = datetime.now()

            return account_data

        except Exception as e:
            logger.error(f"查询账户状态失败: {e}")
            return {
                "cash": {"HKD": 0, "USD": 0},
                "buy_power": {"HKD": 0, "USD": 0},
                "net_assets": {"HKD": 0, "USD": 0},
                "positions": {},
                "position_count": 0
            }

    def _display_account_info(self, account):
        """显示账户信息（增强版）"""
        logger.info("\n📈 账户状态:")

        # 显示可用资金（现金或购买力）
        for currency, amount in account["cash"].items():
            logger.info(f"  💰 {currency} 可用资金: ${amount:,.2f}")

        # 显示购买力（如果与可用资金不同）
        if "buy_power" in account:
            for currency, power in account["buy_power"].items():
                if power != account["cash"].get(currency, 0):
                    logger.info(f"  💳 {currency} 购买力: ${power:,.2f}")

        # 显示净资产
        if "net_assets" in account:
            for currency, assets in account["net_assets"].items():
                if assets > 0:
                    logger.info(f"  💎 {currency} 净资产: ${assets:,.2f}")

        # 显示风控参数
        logger.info(f"\n  📊 风控状态:")
        logger.info(f"    • 持仓数: {account['position_count']}/{self.max_positions}")

        # 显示各市场持仓数
        if account["positions"]:
            market_counts = {}
            for symbol in account["positions"]:
                market = self._get_market(symbol)
                market_counts[market] = market_counts.get(market, 0) + 1

            market_info = []
            for market in ['HK', 'US', 'SH', 'SZ']:
                count = market_counts.get(market, 0)
                limit = self.max_positions_by_market.get(market, 5)
                if count > 0 or market in ['HK', 'US']:  # 显示有持仓的或主要市场
                    market_info.append(f"{market}:{count}/{limit}")

            if market_info:
                logger.info(f"    • 分市场: {', '.join(market_info)}")

        logger.info(f"    • 每标的日交易上限: {self.max_daily_trades_per_symbol}次")

        # 显示今日交易统计
        if self.executed_today:
            total_trades = sum(self.executed_today.values())
            logger.info(f"    • 今日已交易: {total_trades}笔 ({len(self.executed_today)}个标的)")

        # 显示持仓详情
        if account["positions"]:
            logger.info(f"\n  📦 持仓详情:")
            for symbol, pos in account["positions"].items():
                stop_info = ""
                if symbol in self.positions_with_stops:
                    stops = self.positions_with_stops[symbol]
                    stop_info = f" | 止损: ${stops['stop_loss']:.2f} | 止盈: ${stops['take_profit']:.2f}"
                logger.info(f"    - {symbol}: {pos['quantity']}股 @ ${pos['cost']:.2f}{stop_info}")

    async def _can_open_position(self, symbol, account):
        """检查是否可以开仓（查询数据库）"""
        # 检查今日交易次数
        trade_count = self.executed_today.get(symbol, 0)

        # 如果已达到每日最大交易次数
        if trade_count >= self.max_daily_trades_per_symbol:
            logger.warning(f"  ❌ {symbol}: 今日已交易{trade_count}次，达到上限({self.max_daily_trades_per_symbol}次)")
            return False

        # 从数据库检查是否有未完成的买单
        try:
            today_orders = await self.order_manager.get_today_orders(symbol)
            pending_buy_count = sum(1 for o in today_orders
                                   if o.side == "BUY" and o.status in ["New", "WaitToNew", "PartialFilled"])
            if pending_buy_count > 0:
                logger.warning(f"  ❌ {symbol}: 有{pending_buy_count}个待成交买单，跳过")
                return False
        except Exception as e:
            logger.debug(f"  数据库查询失败，检查缓存: {e}")

        # 检查是否已持有
        if symbol in account["positions"]:
            logger.warning(f"  ❌ {symbol}: 已持有，跳过买入信号")
            return False

        # 检查缓存中是否有未完成的买单
        if self._has_pending_buy_order(symbol):
            logger.warning(f"  ❌ {symbol}: 缓存中有未完成的买单，跳过")
            return False

        # 检查市场持仓限制（避免单一市场过度集中）
        market = self._get_market(symbol)
        market_positions = [s for s in account["positions"] if self._get_market(s) == market]
        market_limit = self.max_positions_by_market.get(market, 5)  # 默认5个

        if len(market_positions) >= market_limit:
            logger.warning(
                f"  ❌ {symbol}: {market}市场已达持仓上限 "
                f"({len(market_positions)}/{market_limit})"
            )
            return False

        # 检查总持仓数
        if account["position_count"] < self.max_positions:
            logger.info(
                f"  ✅ {symbol}: 可以开仓 "
                f"({market}: {len(market_positions)}/{market_limit}, "
                f"总: {account['position_count']}/{self.max_positions})"
            )
            return True

        # 如果已满仓，返回False（需要通过 _try_make_room 来清理）
        logger.warning(f"  ⚠️ {symbol}: 已达最大持仓数({self.max_positions})，需要清理仓位")
        return False

    async def _init_today_orders(self):
        """初始化今日订单缓存并同步到数据库"""
        try:
            logger.info("📋 同步今日订单到数据库...")

            # 使用OrderManager同步券商订单到数据库
            sync_result = await self.order_manager.sync_with_broker(self.trade_client)

            # 统计每个标的的交易次数
            self.executed_today = {}
            all_buy_orders = await self.order_manager.get_today_orders()
            for order in all_buy_orders:
                if order.side == "BUY":
                    # 统计每个标的的买单次数（包括成交和待成交）
                    self.executed_today[order.symbol] = self.executed_today.get(order.symbol, 0) + 1

            # 获取今日所有待成交订单的详细信息
            for symbol in sync_result["pending"]:
                # 从数据库获取待成交订单信息
                today_orders = await self.order_manager.get_today_orders(symbol)
                for order in today_orders:
                    if order.side == "BUY" and order.status in ["New", "WaitToNew"]:
                        self.pending_orders[symbol] = {
                            'order_id': order.order_id,
                            'timestamp': order.created_at,
                            'side': 'BUY',
                            'quantity': order.quantity,
                            'status': order.status
                        }
                        break  # 只取最新的待成交买单

            # 获取今日所有买入的标的（从数据库）
            db_buy_symbols = await self.order_manager.get_today_buy_symbols()

            # 显示汇总信息
            logger.info(f"\n📊 今日订单汇总（数据库）:")
            logger.info(f"  ✅ 已成交买入: {len(sync_result['executed'])} 个标的")
            if sync_result['executed']:
                logger.info(f"     {', '.join(sorted(sync_result['executed']))}")

            logger.info(f"  ⏳ 待成交买单: {len(sync_result['pending'])} 个")
            if sync_result['pending']:
                logger.info(f"     {', '.join(sorted(sync_result['pending']))}")

            logger.info(f"  📁 数据库已记录买单: {len(db_buy_symbols)} 个")
            if db_buy_symbols:
                logger.info(f"     {', '.join(sorted(db_buy_symbols))}")

        except Exception as e:
            logger.error(f"初始化订单数据库失败: {e}")
            logger.error("将使用内存缓存作为备选方案")

    async def _refresh_today_orders(self):
        """刷新今日订单缓存（从数据库同步）"""
        try:
            # 同步最新的券商订单到数据库
            sync_result = await self.order_manager.sync_with_broker(self.trade_client)

            # 获取数据库中所有今日买单
            db_buy_symbols = await self.order_manager.get_today_buy_symbols()

            # 更新缓存
            new_executed = 0
            new_pending = 0

            # 重新统计每个标的的交易次数
            self.executed_today = {}
            all_buy_orders = await self.order_manager.get_today_orders()
            for order in all_buy_orders:
                if order.side == "BUY":
                    self.executed_today[order.symbol] = self.executed_today.get(order.symbol, 0) + 1

            # 计算新增的交易
            for symbol in sync_result["executed"]:
                if self.executed_today.get(symbol, 0) > 0:
                    new_executed += 1

                # 如果之前在pending_orders中，移除它
                if symbol in self.pending_orders:
                    del self.pending_orders[symbol]

            # 处理待成交的订单
            for symbol in sync_result["pending"]:
                if symbol not in self.pending_orders and symbol not in self.executed_today:
                    # 从数据库获取订单详情
                    today_orders = await self.order_manager.get_today_orders(symbol)
                    for order in today_orders:
                        if order.side == "BUY" and order.status in ["New", "WaitToNew"]:
                            self.pending_orders[symbol] = {
                                'order_id': order.order_id,
                                'timestamp': order.created_at,
                                'side': 'BUY',
                                'quantity': order.quantity,
                                'status': order.status
                            }
                            new_pending += 1
                            logger.info(f"  🆕 发现新待成交买单: {symbol}")
                            break

            if new_executed > 0 or new_pending > 0:
                logger.info(f"  📊 更新: 新增 {new_executed} 个已成交，{new_pending} 个待成交")
                logger.info(f"  📈 当前: {len(self.executed_today)} 个已成交，{len(self.pending_orders)} 个待成交")
                logger.info(f"  📁 数据库记录: {len(db_buy_symbols)} 个今日买单")

        except Exception as e:
            logger.debug(f"刷新订单失败: {e}")

    def _has_pending_buy_order(self, symbol):
        """检查是否有未完成的买单"""
        if symbol not in self.pending_orders:
            return False

        order_info = self.pending_orders[symbol]

        # 检查是否是买单
        if order_info.get('side') != 'BUY':
            return False

        # 检查订单是否已超时（5分钟）
        if datetime.now() - order_info['timestamp'] > timedelta(seconds=self.order_cache_timeout):
            logger.debug(f"  清理超时订单缓存: {symbol}")
            del self.pending_orders[symbol]
            return False

        return True

    async def _update_order_status(self, symbol):
        """更新订单状态（同步到数据库）"""
        if symbol not in self.pending_orders:
            return

        try:
            order_id = self.pending_orders[symbol]['order_id']
            order_detail = await self.trade_client.order_detail(order_id)

            # 转换状态为字符串
            status_str = str(order_detail.status).replace("OrderStatus.", "")

            # 更新数据库中的订单状态
            await self.order_manager.update_order_status(order_id, status_str)

            if order_detail.status == openapi.OrderStatus.Filled:
                # 订单已成交
                self.executed_today.add(symbol)
                del self.pending_orders[symbol]
                logger.debug(f"  ✅ {symbol}: 订单已成交（数据库已更新）")

            elif order_detail.status in [
                openapi.OrderStatus.Canceled,  # 注意是 Canceled 不是 Cancelled
                openapi.OrderStatus.Expired,
                openapi.OrderStatus.Rejected
            ]:
                # 订单已取消/过期/拒绝
                del self.pending_orders[symbol]
                logger.debug(f"  ❌ {symbol}: 订单已取消/过期（数据库已更新）")

        except Exception as e:
            logger.debug(f"更新订单状态失败: {e}")

    async def _try_make_room(self, new_signal, account):
        """
        智能仓位管理：当满仓时，评估是否应该清理弱势持仓为新信号腾出空间

        清理优先级（从高到低）：
        1. 已触发止损但未执行的持仓
        2. 亏损接近止损位的持仓（评分低）
        3. 盈利但技术指标转弱的持仓
        4. 盈利最少的持仓

        Returns:
            bool: 是否成功腾出空间（通过执行卖出）
        """
        # 注意：这个函数只在满仓时被调用，所以不需要再次检查
        logger.debug(f"  📊 评估仓位清理: 当前持仓数 {account['position_count']}/{self.max_positions}")

        # 只有较强的买入信号才考虑清理（降低门槛，从STRONG_BUY扩展到BUY）
        if new_signal['type'] not in ['STRONG_BUY', 'BUY', 'WEAK_BUY']:
            logger.debug(f"  ❌ 新信号类型 {new_signal['type']} 不足以触发仓位清理")
            return False

        # 使用智能持仓轮换系统进行更精确的评估
        try:
            # 尝试导入智能持仓轮换模块（如果可用）
            from smart_position_rotation import SmartPositionRotator
            rotator = SmartPositionRotator()

            # 使用智能轮换系统评估
            rotation_success = await rotator.execute_position_rotation(
                new_signal, self.trade_client, self.quote_client
            )

            if rotation_success:
                logger.success("  ✅ 智能持仓轮换成功，已腾出空间")
                return True
            else:
                logger.info("  ℹ️ 智能轮换评估后决定保留当前持仓")
                # 继续使用传统方法作为备选

        except ImportError:
            logger.debug("  使用内置仓位清理逻辑")
        except Exception as e:
            logger.error(f"  智能轮换失败: {e}, 使用备选方案")

        # 评估所有持仓的质量
        positions_to_evaluate = []

        for symbol, position in account["positions"].items():
            # 跳过已有待处理卖单的持仓（避免重复提交）
            if symbol in self.pending_orders and self.pending_orders[symbol].get('side') == 'SELL':
                logger.debug(f"  ⏭️  {symbol}: 已有待处理卖单，跳过评估")
                continue

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

            # 1. 盈亏评分（-20分到+30分）- 优化评分体系
            if pnl_pct < -5:
                score -= 20  # 大幅亏损
            elif pnl_pct < -3:
                score -= 15  # 中等亏损
            elif pnl_pct < -1:
                score -= 10  # 小幅亏损
            elif pnl_pct < 0:
                score -= 5   # 微亏
            elif pnl_pct > 15:
                score += 30  # 大幅盈利
            elif pnl_pct > 10:
                score += 25  # 盈利良好
            elif pnl_pct > 5:
                score += 15  # 中等盈利
            elif pnl_pct > 2:
                score += 10  # 小幅盈利
            else:
                score += 5   # 微盈

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

        # 决策逻辑（优化后的清理策略）
        should_clear = False
        clear_reason = ""

        if weakest['score'] == 0:
            # 已触发止损
            should_clear = True
            clear_reason = "已触发止损"
        elif weakest['score'] < 30 and new_signal_score > 60:
            # 弱势持仓 + 较强新信号
            should_clear = True
            clear_reason = f"弱势持仓(评分:{weakest['score']}) vs 强信号(评分:{new_signal_score})"
        elif weakest['pnl_pct'] < -2 and new_signal_score > 50:
            # 亏损持仓 + 中等新信号
            should_clear = True
            clear_reason = f"亏损持仓({weakest['pnl_pct']:.1f}%) vs 新信号(评分:{new_signal_score})"
        elif weakest['score'] < 50 and new_signal_score - weakest['score'] > 15:
            # 评分差距显著（优化：从20分降低到15分，从40分提高到50分）
            should_clear = True
            clear_reason = f"评分差距显著: 持仓({weakest['score']}) vs 新信号({new_signal_score})"
        elif weakest['pnl_pct'] < 2 and new_signal['type'] == 'STRONG_BUY':
            # 低收益持仓遇到强买入信号
            should_clear = True
            clear_reason = f"低收益持仓({weakest['pnl_pct']:.1f}%) vs 强买入信号"
        elif weakest['pnl_pct'] < 5 and new_signal_score >= 60:
            # 新增：中等收益持仓遇到高分信号（60+分）应该换仓
            should_clear = True
            clear_reason = f"中等收益持仓({weakest['pnl_pct']:.1f}%) vs 高分信号({new_signal_score})"
        elif new_signal_score >= 60 and weakest['score'] < 55:
            # 新增：强信号(≥60分) vs 一般持仓(<55分)，果断换仓
            should_clear = True
            clear_reason = f"强信号({new_signal_score}) vs 一般持仓(评分:{weakest['score']})"

        if should_clear:
            # 获取中文名称用于显示
            weakest_name = self._get_symbol_name(weakest['symbol'])
            weakest_display = f"{weakest['symbol']} ({weakest_name})" if weakest_name else weakest['symbol']
            new_symbol_name = self._get_symbol_name(new_signal.get('symbol', ''))
            new_symbol_display = f"{new_signal.get('symbol', 'N/A')} ({new_symbol_name})" if new_symbol_name else new_signal.get('symbol', 'N/A')

            logger.info(
                f"\n🔄 智能仓位管理决策: 执行清理\n"
                f"   清理标的: {weakest_display}\n"
                f"   原因: {clear_reason}\n"
                f"   持仓评分: {weakest['score']}/100, 盈亏: {weakest['pnl_pct']:.2f}%\n"
                f"   新信号: {new_symbol_display}\n"
                f"   新信号类型: {new_signal['type']}, 评分: {new_signal_score}/100"
            )

            # 发送Slack通知
            if self.slack:
                message = (
                    f"🔄 *智能仓位管理*\n\n"
                    f"📊 清理持仓: {weakest_display}\n"
                    f"💯 持仓评分: {weakest['score']}/100\n"
                    f"📈 盈亏: {weakest['pnl_pct']:.2f}%\n"
                    f"💡 原因: {clear_reason}\n\n"
                    f"🆕 新信号: {new_symbol_display}\n"
                    f"🎯 信号类型: {new_signal['type']}\n"
                    f"⭐ 新信号评分: {new_signal_score}/100\n"
                    f"✨ 为更优质的机会腾出空间"
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

        # 未执行清理的详细说明
        weakest_name = self._get_symbol_name(weakest['symbol'])
        weakest_display = f"{weakest['symbol']} ({weakest_name})" if weakest_name else weakest['symbol']

        logger.info(
            f"  📊 仓位评估结果: 保持当前持仓\n"
            f"     最弱持仓: {weakest_display}\n"
            f"     持仓评分: {weakest['score']}/100, 盈亏: {weakest['pnl_pct']:.2f}%\n"
            f"     新信号评分: {new_signal_score}/100\n"
            f"     决策: 当前持仓质量尚可，暂不清理"
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
            # 显示开始分析的股票信息
            symbol_name = self._get_symbol_name(symbol)
            symbol_display = f"{symbol} ({symbol_name})" if symbol_name else symbol

            logger.info(f"\n📊 分析 {symbol_display}")
            logger.info(f"  实时行情: 价格=${current_price:.2f}, 成交量={quote.volume:,}")
            # 获取历史K线数据 - 增加天数以获得更完整的MACD数据
            from datetime import timedelta
            end_date = datetime.now()
            # 对ETF使用更少的历史天数
            is_etf = any(etf in symbol for etf in ['2800', '2822', '2828', '3188', '9919', '3110', '2801', '2827', '9067', '2819'])
            # 增加历史数据天数：ETF 60天，普通股票 100天（确保MACD有足够数据）
            days_to_fetch = 60 if is_etf else 100

            start_date = end_date - timedelta(days=days_to_fetch)

            logger.debug(f"  📥 获取历史K线数据: {days_to_fetch}天 (从{start_date.date()}到{end_date.date()})")

            try:
                candles = await self.quote_client.get_history_candles(
                    symbol=symbol,
                    period=openapi.Period.Day,
                    adjust_type=openapi.AdjustType.NoAdjust,
                    start=start_date,
                    end=end_date
                )
                logger.debug(f"  ✅ 获取到 {len(candles) if candles else 0} 天K线数据")
            except Exception as e:
                logger.warning(f"  ❌ 获取K线数据失败: {e}")
                logger.debug(f"     详细错误: {type(e).__name__}: {str(e)}")
                raise  # 重新抛出异常，让外层统一处理

            if not candles or len(candles) < 30:  # 降低最小要求
                logger.warning(
                    f"  ❌ 历史数据不足，跳过分析\n"
                    f"     实际: {len(candles) if candles else 0}天\n"
                    f"     需要: 至少30天"
                )
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
            logger.debug(f"  🔬 开始计算技术指标 (数据长度: {len(closes)}天)...")
            indicators = self._calculate_all_indicators(closes, highs, lows, volumes)
            logger.debug(f"  ✅ 技术指标计算完成")

            # 显示技术指标值
            logger.info("  技术指标:")

            # RSI状态
            rsi_val = indicators.get('rsi', 0)
            if rsi_val < 30:
                rsi_status = "超卖"
            elif rsi_val < 40:
                rsi_status = "偏低"
            elif rsi_val > 70:
                rsi_status = "超买"
            elif rsi_val > 60:
                rsi_status = "偏高"
            else:
                rsi_status = "中性"
            logger.info(f"    RSI: {rsi_val:.1f} ({rsi_status})")

            # 布林带位置
            bb_upper = indicators.get('bb_upper', 0)
            bb_lower = indicators.get('bb_lower', 0)
            bb_middle = indicators.get('bb_middle', 0)
            if bb_upper > bb_lower:
                bb_position = (current_price - bb_lower) / (bb_upper - bb_lower) * 100
                if bb_position < 20:
                    bb_status = "接近下轨"
                elif bb_position > 80:
                    bb_status = "接近上轨"
                else:
                    bb_status = f"{bb_position:.0f}%位置"
            else:
                bb_status = "N/A"
            logger.info(f"    布林带: {bb_status}")

            # MACD状态
            macd_line = indicators.get('macd_line', 0)
            macd_signal = indicators.get('macd_signal', 0)
            macd_hist = indicators.get('macd_histogram', 0)
            if macd_line > macd_signal:
                macd_status = "多头"
            else:
                macd_status = "空头"
            logger.info(f"    MACD: {macd_line:.3f} vs 信号线{macd_signal:.3f} ({macd_status})")

            # 成交量 - 需要计算当前成交量与历史平均的比率
            current_volume = quote.volume if quote.volume else 0
            volume_avg = indicators.get('volume_sma', 0)
            if volume_avg and volume_avg > 0:
                volume_ratio = float(current_volume) / float(volume_avg)
            else:
                volume_ratio = 1.0

            if volume_ratio > 1.5:
                vol_status = "放量"
            elif volume_ratio < 0.5:
                vol_status = "缩量"
            else:
                vol_status = "正常"
            logger.info(f"    成交量: {volume_ratio:.2f}x ({vol_status}), 当前={current_volume:,}")

            # 趋势
            sma20 = indicators.get('sma_20', 0)
            sma50 = indicators.get('sma_50', 0)
            if sma20 > sma50:
                trend_status = "上升趋势"
            else:
                trend_status = "下降趋势"
            logger.info(f"    趋势: {trend_status} (SMA20=${sma20:.2f}, SMA50=${sma50:.2f})")

            # 检查指标有效性
            if not self._validate_indicators(indicators):
                logger.info("  ❌ 技术指标无效，跳过分析")
                return None

            # 分析买入信号
            signal = self._analyze_buy_signals(
                symbol, current_price, quote, indicators, closes, highs, lows
            )

            return signal

        except Exception as e:
            # 分类处理不同的错误，提供详细信息
            error_msg = str(e)
            error_type = type(e).__name__

            if "301607" in error_msg:
                logger.warning(f"  ⚠️ API限制: 请求过于频繁，跳过 {symbol}")
            elif "301600" in error_msg:
                logger.warning(f"  ⚠️ 无权限访问: {symbol}")
            elif "404001" in error_msg:
                logger.warning(f"  ⚠️ 标的不存在或代码错误: {symbol}")
            elif "timeout" in error_msg.lower():
                logger.warning(f"  ⚠️ 获取数据超时: {symbol}")
            else:
                # 显示完整的错误信息供调试
                logger.error(
                    f"  ❌ 分析失败: {symbol}\n"
                    f"     错误类型: {error_type}\n"
                    f"     错误信息: {error_msg}"
                )
                # 在DEBUG级别显示堆栈跟踪
                import traceback
                logger.debug(f"     堆栈跟踪:\n{traceback.format_exc()}")

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
                'current_volume': volumes[-1] if len(volumes) > 0 else np.nan,  # 最新一天的成交量
                'atr': atr[-1] if len(atr) > 0 else np.nan,
                'sma_20': sma_20[-1] if len(sma_20) > 0 else np.nan,
                'sma_50': sma_50[-1] if len(sma_50) > 0 else np.nan,
                # 前一期数据用于判断交叉
                'prev_macd_histogram': macd['histogram'][-2] if len(macd['histogram']) > 1 else 0,
            }
        except Exception as e:
            logger.error(
                f"计算技术指标失败:\n"
                f"  错误类型: {type(e).__name__}\n"
                f"  错误信息: {e}\n"
                f"  数据长度: closes={len(closes)}, highs={len(highs)}, "
                f"lows={len(lows)}, volumes={len(volumes)}"
            )
            # 在DEBUG级别显示堆栈跟踪
            import traceback
            logger.debug(f"  堆栈跟踪:\n{traceback.format_exc()}")

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
        # 注意：quote.volume 是今日累计成交量，需要与历史日成交量比较
        current_volume = quote.volume if quote.volume else 0

        # 如果volume_sma有效，计算比率
        if ind['volume_sma'] and ind['volume_sma'] > 0:
            volume_ratio = float(current_volume) / float(ind['volume_sma'])
        else:
            # 如果没有历史成交量数据，默认为1.0
            volume_ratio = 1.0

        # 调试日志
        logger.debug(f"    成交量计算: 当前={current_volume:,}, 平均={ind.get('volume_sma', 0):,.0f}, 比率={volume_ratio:.2f}")

        # 计算布林带位置
        bb_range = ind['bb_upper'] - ind['bb_lower']
        if bb_range > 0:
            bb_position_pct = (current_price - ind['bb_lower']) / bb_range * 100
        else:
            bb_position_pct = 50

        bb_width_pct = bb_range / ind['bb_middle'] * 100 if ind['bb_middle'] > 0 else 0

        # 开始评分日志
        logger.info("\n  信号评分:")

        # === 1. RSI分析 (0-30分) ===
        rsi_score = 0
        rsi_reason = ""
        if ind['rsi'] < 20:  # 极度超卖（逆向策略）
            rsi_score = 30
            rsi_reason = f"极度超卖({ind['rsi']:.1f})"
            reasons.append(f"RSI{rsi_reason}")
        elif ind['rsi'] < self.rsi_oversold:  # 超卖（逆向策略）
            rsi_score = 25
            rsi_reason = f"超卖({ind['rsi']:.1f})"
            reasons.append(f"RSI{rsi_reason}")
        elif ind['rsi'] < 40:  # 接近超卖
            rsi_score = 15
            rsi_reason = f"偏低({ind['rsi']:.1f})"
            reasons.append(f"RSI{rsi_reason}")
        elif 40 <= ind['rsi'] <= 50:  # 中性偏低
            rsi_score = 5
            rsi_reason = f"中性({ind['rsi']:.1f})"
            reasons.append(f"RSI{rsi_reason}")
        elif 50 < ind['rsi'] <= 70:  # 强势区间（趋势跟随策略）
            rsi_score = 15
            rsi_reason = f"强势({ind['rsi']:.1f})"
            reasons.append(f"RSI强势区间({ind['rsi']:.1f})")
        else:  # > 70，超买
            rsi_reason = f"超买({ind['rsi']:.1f})"

        logger.info(f"    RSI得分: {rsi_score}/30 ({rsi_reason})")
        score += rsi_score

        # === 2. 布林带分析 (0-25分) ===
        bb_score = 0
        bb_reason = ""
        if current_price <= ind['bb_lower']:  # 触及或突破下轨（逆向策略）
            bb_score = 25
            bb_reason = f"触及下轨(${ind['bb_lower']:.2f})"
            reasons.append(f"触及布林带下轨(${ind['bb_lower']:.2f})")
        elif current_price <= ind['bb_lower'] * 1.02:  # 接近下轨（逆向策略）
            bb_score = 20
            bb_reason = "接近下轨"
            reasons.append(f"接近布林带下轨")
        elif bb_position_pct < 30:  # 在下半部
            bb_score = 10
            bb_reason = f"下半部({bb_position_pct:.0f}%)"
            reasons.append(f"布林带下半部({bb_position_pct:.0f}%)")
        elif current_price >= ind['bb_upper']:  # 突破上轨（趋势跟随策略）
            bb_score = 20
            bb_reason = f"突破上轨(${ind['bb_upper']:.2f})"
            reasons.append(f"突破布林带上轨(${ind['bb_upper']:.2f})")
        elif current_price >= ind['bb_upper'] * 0.98:  # 接近上轨（趋势跟随策略）
            bb_score = 15
            bb_reason = "接近上轨"
            reasons.append(f"接近布林带上轨")
        else:
            bb_reason = f"位置{bb_position_pct:.0f}%"

        # 布林带收窄加分
        if bb_width_pct < 10:
            bb_score += 5
            bb_reason += f", 极度收窄({bb_width_pct:.1f}%)"
            reasons.append(f"布林带极度收窄({bb_width_pct:.1f}%)")
        elif bb_width_pct < 15:
            bb_score += 3
            bb_reason += ", 收窄"
            reasons.append(f"布林带收窄")

        logger.info(f"    布林带得分: {bb_score}/25 ({bb_reason})")
        score += bb_score

        # === 3. MACD分析 (0-20分) ===
        macd_score = 0
        macd_reason = ""
        # MACD金叉: histogram从负转正
        if ind['macd_histogram'] > 0 and ind['prev_macd_histogram'] <= 0:
            macd_score = 20
            macd_reason = "金叉(刚上穿)"
            reasons.append("MACD金叉(刚上穿)")
        elif ind['macd_histogram'] > 0 and ind['macd_line'] > ind['macd_signal']:
            macd_score = 15
            macd_reason = "多头"
            reasons.append("MACD多头")
        elif ind['macd_histogram'] > ind['prev_macd_histogram'] > 0:
            macd_score = 10
            macd_reason = "柱状图扩大"
            reasons.append("MACD柱状图扩大")
        else:
            macd_reason = f"空头或中性"

        logger.info(f"    MACD得分: {macd_score}/20 ({macd_reason})")
        score += macd_score

        # === 4. 成交量确认 (0-15分) ===
        volume_score = 0
        vol_reason = ""
        if volume_ratio >= 2.0:  # 放量2倍以上
            volume_score = 15
            vol_reason = f"大幅放量({volume_ratio:.1f}x)"
            reasons.append(f"成交量大幅放大({volume_ratio:.1f}x)")
        elif volume_ratio >= self.volume_surge_threshold:  # 放量1.5倍
            volume_score = 10
            vol_reason = f"放量({volume_ratio:.1f}x)"
            reasons.append(f"成交量放大({volume_ratio:.1f}x)")
        elif volume_ratio >= 1.2:  # 温和放量
            volume_score = 5
            vol_reason = f"温和放量({volume_ratio:.1f}x)"
            reasons.append(f"成交量温和({volume_ratio:.1f}x)")
        elif volume_ratio >= 0.8:  # 正常成交量（支持趋势跟随）
            volume_score = 3
            vol_reason = f"正常({volume_ratio:.1f}x)"
            reasons.append(f"成交量正常({volume_ratio:.1f}x)")
        else:
            vol_reason = f"缩量({volume_ratio:.1f}x)"

        logger.info(f"    成交量得分: {volume_score}/15 ({vol_reason})")
        score += volume_score

        # === 5. 趋势确认 (0-10分) ===
        trend_score = 0
        trend_reason = ""
        if self.use_multi_timeframe:
            # 价格在20日均线上方
            if current_price > ind['sma_20']:
                trend_score += 3
                reasons.append("价格在SMA20上方")

            # 短期均线在长期均线上方(金叉)
            if ind['sma_20'] > ind['sma_50']:
                trend_score += 7
                trend_reason = "上升趋势"
                reasons.append("SMA20在SMA50上方(上升趋势)")
            elif ind['sma_20'] > ind['sma_50'] * 0.98:  # 接近金叉
                trend_score += 4
                trend_reason = "接近金叉"
                reasons.append("接近均线金叉")
            else:
                trend_reason = "下降趋势"

        logger.info(f"    趋势得分: {trend_score}/10 ({trend_reason})")
        score += trend_score

        # 显示总分
        logger.info(f"    总分: {score}/100")

        # === 生成信号 ===
        if score >= 60:  # 强买入信号
            signal_type = "STRONG_BUY"
            logger.info(f"\n  ✅ 决策: 生成强买入信号 (得分{score} >= 60)")
        elif score >= 45:  # 买入信号
            signal_type = "BUY"
            logger.info(f"\n  ✅ 决策: 生成买入信号 (得分{score} >= 45)")
        elif score >= 30:  # 弱买入信号
            signal_type = "WEAK_BUY"
            logger.info(f"\n  ⚠️ 决策: 生成弱买入信号 (得分{score} >= 30)")
        else:
            logger.info(f"\n  ❌ 决策: 不生成信号 (得分{score} < 30最低要求)")
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

        优先级: 止损止盈信号具有最高优先级，确保及时执行
        """
        exit_signals = []  # 收集所有止损止盈信号

        # 添加日志显示开始检查（改为info级别以确保显示）
        if account["positions"]:
            logger.info(f"\n📍 开始检查 {len(account['positions'])} 个持仓的止损止盈状态...")
            logger.info(f"   持仓列表: {list(account['positions'].keys())}")
            logger.info(f"   已设置止损止盈的持仓: {list(self.positions_with_stops.keys())}")

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
                logger.info(f"  {symbol}: 未找到止损止盈记录，尝试设置...")
                try:
                    await self._set_stops_for_position(symbol, entry_price)
                    # 如果成功设置，保存到数据库
                    if symbol in self.positions_with_stops:
                        stops = self.positions_with_stops[symbol]
                        await self.stop_manager.save_stop(
                            symbol=symbol,
                            entry_price=entry_price,
                            stop_loss=stops['stop_loss'],
                            take_profit=stops['take_profit'],
                            atr=stops.get('atr'),
                            quantity=position.get('quantity')
                        )
                except Exception as e:
                    logger.warning(f"  {symbol}: 无法设置止损止盈 - {e}")
                    continue

            # 再次检查是否成功设置
            if symbol not in self.positions_with_stops:
                logger.warning(f"  ⚠️ {symbol}: 跳过止损止盈检查（未设置）")
                continue

            stops = self.positions_with_stops[symbol]
            stop_loss = stops["stop_loss"]
            take_profit = stops["take_profit"]

            # 计算盈亏
            pnl_pct = (current_price / entry_price - 1) * 100

            # 显示当前状态（改为info级别）
            logger.info(
                f"  📊 {symbol}: 当前价=${current_price:.2f}, 成本=${entry_price:.2f}, "
                f"止损=${stop_loss:.2f}, 止盈=${take_profit:.2f}, 盈亏={pnl_pct:+.1f}%"
            )

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
                    # 获取中文名称
                    symbol_name = self._get_symbol_name(symbol)
                    symbol_display = f"{symbol} ({symbol_name})" if symbol_name else symbol

                    message = (
                        f"🛑 *止损触发*: {symbol_display}\n\n"
                        f"💵 入场价: ${entry_price:.2f}\n"
                        f"💸 当前价: ${current_price:.2f}\n"
                        f"🎯 止损位: ${stop_loss:.2f}\n"
                        f"📉 盈亏: *{pnl_pct:.2f}%*\n"
                        f"⚠️ 将执行卖出操作"
                    )
                    await self.slack.send(message)

                # 直接执行止损卖出（无论WebSocket是否启用）
                logger.info(f"🚨 {symbol}: 立即执行止损卖出")
                await self._execute_sell(symbol, current_price, position, "止损")

                # 更新数据库中的止损止盈状态
                pnl = position['quantity'] * (current_price - entry_price)
                await self.stop_manager.update_stop_status(
                    symbol, 'stopped_out', current_price, pnl
                )
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

                            # 获取中文名称
                            symbol_name = self._get_symbol_name(symbol)
                            symbol_display = f"{symbol} ({symbol_name})" if symbol_name else symbol

                            message = (
                                f"💡 *智能止盈 - 继续持有*: {symbol_display}\n\n"
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
                        # 获取中文名称
                        symbol_name = self._get_symbol_name(symbol)
                        symbol_display = f"{symbol} ({symbol_name})" if symbol_name else symbol

                        message = (
                            f"🎉 *止盈触发 - 执行卖出*: {symbol_display}\n\n"
                            f"💵 入场价: ${entry_price:.2f}\n"
                            f"💰 当前价: ${current_price:.2f}\n"
                            f"🎁 止盈位: ${take_profit:.2f}\n"
                            f"📈 盈亏: *+{pnl_pct:.2f}%*\n"
                            f"✅ 将执行卖出操作"
                        )
                        await self.slack.send(message)

                    # 如果启用了WebSocket，加入优先级队列
                    # 直接执行止盈卖出（无论WebSocket是否启用）
                    logger.info(f"💰 {symbol}: 立即执行止盈卖出")
                    await self._execute_sell(symbol, current_price, position, "止盈")

                    # 更新数据库中的止损止盈状态
                    pnl = position['quantity'] * (current_price - entry_price)
                    await self.stop_manager.update_stop_status(
                        symbol, 'took_profit', current_price, pnl
                    )
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

    async def check_realtime_stop_loss(self, symbol, current_price, position):
        """
        实时检查单个标的的止损止盈

        用于WebSocket实时行情推送时立即检查
        返回: (是否触发, 触发类型)
        """
        try:
            # 检查是否有设置止损止盈（仅使用内存缓存，避免频繁数据库查询）
            if symbol not in self.positions_with_stops:
                # ⚠️ 不再在实时检查中查询数据库，避免高频DB访问导致连接泄漏
                # 止损设置应该在买入时就设置好，并加载到内存中
                return False, None

            stops = self.positions_with_stops[symbol]
            stop_loss = stops["stop_loss"]
            take_profit = stops["take_profit"]
            entry_price = position["cost"]

            # 计算盈亏
            pnl_pct = (current_price / entry_price - 1) * 100

            # 实时日志（只在接近止损止盈时显示）
            if abs(current_price - stop_loss) / stop_loss < 0.02 or \
               abs(current_price - take_profit) / take_profit < 0.02:
                logger.info(
                    f"⚡ 实时监控 {symbol}: 价格=${current_price:.2f}, "
                    f"止损=${stop_loss:.2f}, 止盈=${take_profit:.2f}, 盈亏={pnl_pct:+.1f}%"
                )

            # 检查止损
            if current_price <= stop_loss:
                logger.warning(
                    f"\n🛑 {symbol} 实时触发止损!\n"
                    f"   当前价: ${current_price:.2f}\n"
                    f"   止损位: ${stop_loss:.2f}\n"
                    f"   盈亏: {pnl_pct:.2f}%"
                )

                # 立即执行止损
                await self._execute_sell(symbol, current_price, position, "实时止损")

                # 更新数据库状态
                pnl = position['quantity'] * (current_price - entry_price)
                await self.stop_manager.update_stop_status(
                    symbol, 'stopped_out', current_price, pnl
                )

                return True, "STOP_LOSS"

            # 检查止盈
            elif current_price >= take_profit:
                logger.success(
                    f"\n🎉 {symbol} 实时触发止盈!\n"
                    f"   当前价: ${current_price:.2f}\n"
                    f"   止盈位: ${take_profit:.2f}\n"
                    f"   盈亏: {pnl_pct:.2f}%"
                )

                # 立即执行止盈
                await self._execute_sell(symbol, current_price, position, "实时止盈")

                # 更新数据库状态
                pnl = position['quantity'] * (current_price - entry_price)
                await self.stop_manager.update_stop_status(
                    symbol, 'took_profit', current_price, pnl
                )

                return True, "TAKE_PROFIT"

            return False, None

        except Exception as e:
            logger.error(f"实时止损止盈检查失败 {symbol}: {e}")
            return False, None

    async def _execute_sell(self, symbol, current_price, position, reason):
        """执行卖出"""
        try:
            quantity = position["quantity"]

            # 获取实时买卖盘价格并计算智能下单价格
            bid_price = None
            ask_price = None
            atr = None

            try:
                # 获取深度数据（买卖盘）
                depth = await self.quote_client.get_depth(symbol)
                if depth.bids and len(depth.bids) > 0:
                    bid_price = float(depth.bids[0].price)
                if depth.asks and len(depth.asks) > 0:
                    ask_price = float(depth.asks[0].price)
                if bid_price or ask_price:
                    logger.debug(f"  📊 卖出获取买卖盘: 买一=${bid_price:.2f if bid_price else 0}, 卖一=${ask_price:.2f if ask_price else 0}")

                # 尝试获取ATR
                if symbol in self.positions_with_stops and 'atr' in self.positions_with_stops[symbol]:
                    atr = self.positions_with_stops[symbol]['atr']
            except Exception as e:
                logger.debug(f"  ⚠️  获取买卖盘数据失败，使用默认价格计算: {e}")

            order_price = self._calculate_order_price(
                "SELL",
                current_price,
                bid_price=bid_price,
                ask_price=ask_price,
                atr=atr,
                symbol=symbol
            )

            order = await self.trade_client.submit_order({
                "symbol": symbol,
                "side": "SELL",
                "quantity": quantity,
                "price": order_price
            })

            entry_price = position["cost"]
            # Convert Decimal to float for calculation
            pnl = (current_price - entry_price) * float(quantity)
            pnl_pct = (current_price / entry_price - 1) * 100

            # 保存卖单到数据库
            await self.order_manager.save_order(
                order_id=order['order_id'],
                symbol=symbol,
                side="SELL",
                quantity=quantity,
                price=order_price,
                status="New"
            )

            # 记录到pending_orders缓存（避免重复提交卖单）
            self.pending_orders[symbol] = {
                'order_id': order['order_id'],
                'timestamp': datetime.now(),
                'side': 'SELL',
                'quantity': quantity,
                'status': 'submitted'
            }

            logger.success(
                f"\n✅ 平仓订单已提交: {order['order_id']}\n"
                f"   标的: {symbol}\n"
                f"   原因: {reason}\n"
                f"   数量: {quantity}股\n"
                f"   入场价: ${entry_price:.2f}\n"
                f"   下单价: ${order_price:.2f} (当前价: ${current_price:.2f})\n"
                f"   盈亏: ${pnl:.2f} ({pnl_pct:+.2f}%)"
            )

            # 发送Slack通知
            if self.slack:
                emoji = "✅" if pnl > 0 else "❌"
                # 获取中文名称
                symbol_name = self._get_symbol_name(symbol)
                symbol_display = f"{symbol} ({symbol_name})" if symbol_name else symbol

                message = (
                    f"{emoji} *平仓订单已提交*\n\n"
                    f"📋 订单ID: `{order['order_id']}`\n"
                    f"📊 标的: *{symbol_display}*\n"
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

            # 从数据库移除止损止盈记录（标记为已取消）
            await self.stop_manager.remove_stop(symbol)

        except Exception as e:
            logger.error(f"  ❌ {symbol} 平仓失败: {e}")

    def _calculate_dynamic_budget(self, account, signal):
        """
        智能动态预算计算

        根据以下因素动态分配：
        1. 账户总资金和总资产
        2. 当前持仓数量和价值
        3. 信号强度和市场情况
        4. 波动性（ATR）
        5. 风险管理规则
        """
        # 获取账户币种（港币或美元）
        currency = "HKD" if ".HK" in signal.get('symbol', '') else "USD"
        available_cash = account["cash"].get(currency, 0)

        # 确保保留最低现金储备
        usable_cash = max(0, available_cash - self.min_cash_reserve)

        if usable_cash <= 0:
            logger.debug(f"  💰 可用资金不足（需保留${self.min_cash_reserve}储备金）")
            return 0

        # 优先使用净资产（如果有），否则计算总价值
        if "net_assets" in account and currency in account["net_assets"]:
            total_portfolio_value = account["net_assets"][currency]
            logger.debug(f"  使用净资产: ${total_portfolio_value:,.0f}")
        else:
            # 计算账户总价值（现金 + 持仓市值）
            total_portfolio_value = max(0, available_cash)  # 避免负数
            for pos in account["positions"].values():
                # 估算持仓市值（使用成本价作为近似值）
                position_value = pos.get("quantity", 0) * pos.get("cost", 0)
                if pos.get("currency") == currency:
                    total_portfolio_value += position_value

        current_positions = account["position_count"]
        remaining_slots = max(1, self.max_positions - current_positions)

        # 基于账户总价值计算仓位大小（而不是仅基于现金）
        max_position_value = total_portfolio_value * self.max_position_size_pct
        min_position_value = total_portfolio_value * self.min_position_size_pct

        # 基础预算 = 可用现金在剩余仓位间平均分配
        base_budget = usable_cash / remaining_slots if remaining_slots > 0 else 0

        # 根据信号强度调整（更细致的分级）
        signal_strength = signal.get('strength', 50)
        if signal_strength >= 80:  # 极强信号
            strength_multiplier = 1.5
        elif signal_strength >= 70:  # 强信号
            strength_multiplier = 1.3
        elif signal_strength >= 60:  # 较强信号
            strength_multiplier = 1.1
        elif signal_strength >= 50:  # 中等信号
            strength_multiplier = 0.9
        elif signal_strength >= 40:  # 较弱信号
            strength_multiplier = 0.7
        else:  # 弱信号
            strength_multiplier = 0.5

        # 根据波动性（ATR）调整 - Kelly准则启发
        atr = signal.get('atr', 0)
        current_price = signal.get('current_price', 1)
        atr_ratio = (atr / current_price * 100) if current_price > 0 else 0

        if atr_ratio > 8:  # 极高波动
            volatility_multiplier = 0.5
        elif atr_ratio > 5:  # 高波动
            volatility_multiplier = 0.7
        elif atr_ratio > 3:  # 中等波动
            volatility_multiplier = 0.9
        elif atr_ratio > 1.5:  # 正常波动
            volatility_multiplier = 1.0
        else:  # 低波动（稳定）
            volatility_multiplier = 1.2

        # 市场时段调整（美股盘前盘后减少仓位）
        time_multiplier = 1.0
        active_markets, us_session = self.get_active_markets()
        if 'US' in active_markets and us_session in ['premarket', 'afterhours']:
            time_multiplier = 0.7  # 盘前盘后减少30%仓位

        # 计算动态预算
        dynamic_budget = base_budget * strength_multiplier * volatility_multiplier * time_multiplier

        # 应用仓位限制
        # 不能超过账户总价值的max_position_size_pct
        dynamic_budget = min(dynamic_budget, max_position_value)

        # 不能低于最小仓位（但如果资金真的不足，允许为0）
        if dynamic_budget < min_position_value:
            if usable_cash < min_position_value:
                # 资金确实不足，返回所有可用资金
                dynamic_budget = usable_cash
            else:
                # 资金充足但计算出的仓位太小，使用最小仓位
                dynamic_budget = min_position_value

        # 最终检查：不能超过实际可用现金
        final_budget = min(dynamic_budget, usable_cash)

        logger.debug(
            f"  💰 智能预算计算: "
            f"可用现金=${usable_cash:.0f}, "
            f"账户总值=${total_portfolio_value:.0f}, "
            f"剩余仓位={remaining_slots}, "
            f"信号强度={signal_strength}(×{strength_multiplier:.1f}), "
            f"ATR={atr_ratio:.1f}%(×{volatility_multiplier:.1f}), "
            f"最终预算=${final_budget:.0f}"
        )

        return final_budget

    def _adjust_price_to_tick_size(self, price, symbol):
        """根据港股价格档位规则调整价格"""
        if '.HK' not in symbol:
            # 非港股，直接返回保留2位小数
            return round(price, 2)

        # 港股价格档位规则
        if price < 0.25:
            tick_size = 0.001
        elif price < 0.50:
            tick_size = 0.005
        elif price < 10.00:
            tick_size = 0.01
        elif price < 20.00:
            tick_size = 0.02
        elif price < 100.00:
            tick_size = 0.05
        elif price < 200.00:
            tick_size = 0.10
        elif price < 500.00:
            tick_size = 0.20
        elif price < 1000.00:
            tick_size = 0.50
        elif price < 2000.00:
            tick_size = 1.00
        elif price < 5000.00:
            tick_size = 2.00
        else:
            tick_size = 5.00

        # 调整到最近的有效档位
        adjusted_price = round(price / tick_size) * tick_size

        # 确保价格格式正确
        if tick_size >= 1:
            return round(adjusted_price, 0)
        else:
            # 计算需要的小数位数
            decimal_places = len(str(tick_size).split('.')[-1])
            return round(adjusted_price, decimal_places)

    def _calculate_order_price(self, side, current_price, bid_price=None, ask_price=None, atr=None, symbol=None):
        """
        智能计算下单价格（支持港股价格档位）

        买入策略：
        - 使用买一价（bid）的基础上略微加价，提高成交概率
        - 如果没有bid，使用当前价略微减价

        卖出策略：
        - 使用卖一价（ask）的基础上略微减价，提高成交概率
        - 如果没有ask，使用当前价略微加价
        """
        # 计算价格调整幅度（基于ATR或固定比例）
        if atr and current_price > 0:
            price_adjustment = min(atr * 0.1, current_price * 0.002)  # ATR的10%或0.2%，取较小值
        else:
            price_adjustment = current_price * 0.001  # 默认0.1%

        if side.upper() == "BUY":
            if bid_price and bid_price > 0:
                # 在买一价基础上加价，提高成交概率
                order_price = bid_price + price_adjustment
            else:
                # 使用当前价略微减价
                order_price = current_price - price_adjustment
        else:  # SELL
            if ask_price and ask_price > 0:
                # 在卖一价基础上减价，提高成交概率
                order_price = ask_price - price_adjustment
            else:
                # 使用当前价略微加价
                order_price = current_price + price_adjustment

        # 确保价格为正
        order_price = max(order_price, 0.01)

        # 根据交易所规则调整价格档位
        if symbol:
            order_price = self._adjust_price_to_tick_size(order_price, symbol)
        else:
            # 价格取整（保留2位小数）
            order_price = round(order_price, 2)

        # 格式化买卖价格，处理None的情况
        bid_str = f"${bid_price:.2f}" if bid_price is not None else "N/A"
        ask_str = f"${ask_price:.2f}" if ask_price is not None else "N/A"

        logger.debug(
            f"  📊 下单价格计算: "
            f"方向={side}, "
            f"当前价=${current_price:.2f}, "
            f"买一={bid_str}, "
            f"卖一={ask_str}, "
            f"下单价=${order_price:.2f}"
        )

        return order_price

    async def execute_signal(self, symbol, signal, current_price, account):
        """执行开仓信号（带资金验证）"""
        try:
            signal_type = signal['type']
            signal['symbol'] = symbol  # 添加symbol到signal中供动态预算计算使用
            signal['current_price'] = current_price  # 添加当前价格用于波动性计算

            # 弱买入信号需要更严格的条件
            if signal_type == "WEAK_BUY" and signal['strength'] < 35:
                logger.debug(f"  跳过弱买入信号 (评分: {signal['strength']})")
                return

            # 资金合理性检查
            currency = "HKD" if ".HK" in symbol else "USD"
            available_cash = account["cash"].get(currency, 0)

            # 检查资金是否异常
            if available_cash < 0:
                logger.error(
                    f"  ❌ {symbol}: 资金异常（显示为负数: ${available_cash:.2f}）\n"
                    f"     可能原因：融资账户或数据错误\n"
                    f"     购买力: ${account.get('buy_power', {}).get(currency, 0):,.2f}\n"
                    f"     净资产: ${account.get('net_assets', {}).get(currency, 0):,.2f}"
                )
                # 如果有购买力，使用购买力
                if account.get('buy_power', {}).get(currency, 0) > 1000:
                    logger.info(f"  💳 使用购买力进行交易")
                    # 继续执行，因为有购买力
                else:
                    logger.warning(f"  ⏭️  跳过交易，等待资金正常")
                    return

            # 动态计算预算
            dynamic_budget = self._calculate_dynamic_budget(account, signal)

            # 获取股票的交易手数
            lot_size = await self.lot_size_helper.get_lot_size(symbol, self.quote_client)

            # 计算购买数量（必须是手数的整数倍）- 使用动态预算
            quantity = self.lot_size_helper.calculate_order_quantity(
                symbol, dynamic_budget, current_price, lot_size
            )

            if quantity <= 0:
                logger.warning(
                    f"  ⚠️  {symbol}: 动态预算不足以购买1手 "
                    f"(手数: {lot_size}, 需要: ${lot_size * current_price:.2f}, "
                    f"动态预算: ${dynamic_budget:.2f})"
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

            # 获取实时买卖盘价格并计算智能下单价格
            bid_price = None
            ask_price = None
            try:
                # 获取深度数据（买卖盘）
                depth = await self.quote_client.get_depth(symbol)
                if depth.bids and len(depth.bids) > 0:
                    bid_price = float(depth.bids[0].price)
                if depth.asks and len(depth.asks) > 0:
                    ask_price = float(depth.asks[0].price)
                if bid_price or ask_price:
                    logger.debug(f"  📊 获取到买卖盘: 买一=${bid_price:.2f if bid_price else 0}, 卖一=${ask_price:.2f if ask_price else 0}")
            except Exception as e:
                logger.debug(f"  ⚠️  获取买卖盘数据失败，使用默认价格计算: {e}")

            order_price = self._calculate_order_price(
                "BUY",
                current_price,
                bid_price=bid_price,
                ask_price=ask_price,
                atr=signal.get('atr'),
                symbol=symbol
            )

            # 下单
            order = await self.trade_client.submit_order({
                "symbol": symbol,
                "side": "BUY",
                "quantity": quantity,
                "price": order_price
            })

            logger.success(
                f"\n✅ 开仓订单已提交: {order['order_id']}\n"
                f"   标的: {symbol}\n"
                f"   类型: {signal_type}\n"
                f"   评分: {signal['strength']:.0f}/100\n"
                f"   动态预算: ${dynamic_budget:.2f}\n"
                f"   数量: {quantity}股 ({num_lots}手 × {lot_size}股/手)\n"
                f"   下单价: ${order_price:.2f} (当前价: ${current_price:.2f})\n"
                f"   总额: ${order_price * quantity:.2f}\n"
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
                    indicators_text += f"   • 布林带位置: {signal['bb_position']}"
                    # 从字符串中提取数值进行比较
                    try:
                        bb_position_value = float(str(signal['bb_position']).replace('%', ''))
                        if bb_position_value < 20:
                            indicators_text += " (接近下轨 ⬇️)\n"
                        else:
                            indicators_text += "\n"
                    except (ValueError, AttributeError):
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

                # 获取中文名称
                symbol_name = self._get_symbol_name(symbol)
                symbol_display = f"{symbol} ({symbol_name})" if symbol_name else symbol

                message = (
                    f"{emoji} *开仓订单已提交*\n\n"
                    f"📋 订单ID: `{order['order_id']}`\n"
                    f"📊 标的: *{symbol_display}*\n"
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

            # 保存止损止盈到数据库
            await self.stop_manager.save_stop(
                symbol=symbol,
                entry_price=current_price,
                stop_loss=signal['stop_loss'],
                take_profit=signal['take_profit'],
                atr=signal.get('atr'),
                quantity=quantity,
                strategy='advanced_technical'
            )

            # 保存订单到数据库
            await self.order_manager.save_order(
                order_id=order['order_id'],
                symbol=symbol,
                side="BUY",
                quantity=quantity,
                price=order_price,
                status="New"
            )

            # 记录订单到pending_orders缓存
            self.pending_orders[symbol] = {
                'order_id': order['order_id'],
                'timestamp': datetime.now(),
                'side': 'BUY',
                'quantity': quantity,
                'status': 'submitted'
            }

            # 更新交易次数
            self.executed_today[symbol] = self.executed_today.get(symbol, 0) + 1
            logger.debug(f"  📊 {symbol} 今日交易次数: {self.executed_today[symbol]}")

        except Exception as e:
            logger.error(f"  ❌ {symbol} 开仓失败: {e}")
            import traceback
            traceback.print_exc()


async def main():
    """主函数"""
    import sys

    # 检查命令行参数
    use_builtin = "--builtin" in sys.argv or "-b" in sys.argv

    # 解析最大迭代次数
    max_iterations = None
    for i, arg in enumerate(sys.argv):
        if arg in ["--iterations", "-n"]:
            if i + 1 < len(sys.argv):
                try:
                    max_iterations = int(sys.argv[i + 1])
                    logger.info(f"⏱️  设置最大迭代次数: {max_iterations}")
                except ValueError:
                    logger.warning(f"无效的迭代次数参数: {sys.argv[i + 1]}")

    if use_builtin:
        logger.info("\n使用内置监控列表 - 高级技术指标组合策略")
    else:
        logger.info("\n使用配置文件监控列表 - 高级技术指标组合策略")

    trader = AdvancedTechnicalTrader(use_builtin_watchlist=use_builtin, max_iterations=max_iterations)

    try:
        await trader.run()
    except KeyboardInterrupt:
        logger.info("\n收到中断信号，停止交易系统")
    finally:
        # 清理资源
        logger.info("正在清理资源...")
        if hasattr(trader, 'stop_manager') and trader.stop_manager:
            try:
                await trader.stop_manager.disconnect()
                logger.success("✅ 止损管理器已关闭")
            except Exception as e:
                logger.warning(f"关闭止损管理器失败: {e}")

        if hasattr(trader, 'order_manager') and trader.order_manager:
            # OrderManager 使用 SQLAlchemy，连接池会自动管理
            logger.debug("订单管理器使用 SQLAlchemy，自动管理连接池")

        logger.success("✅ 所有资源已清理")


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
║  ⚙️  智能风控系统:                                                      ║
║     • 动态仓位管理:                                                   ║
║       - 根据账户总价值动态计算（非固定金额）                             ║
║       - 最小仓位: 账户总值的5%                                         ║
║       - 最大仓位: 账户总值的30%                                        ║
║       - 根据信号强度调整: 0.5x-1.5x                                   ║
║       - 根据波动性(ATR)调整: 0.5x-1.2x                                ║
║       - 美股盘前盘后自动减仓30%                                        ║
║     • 交易频率控制:                                                   ║
║       - 每个标的每天最多交易2次（可配置）                               ║
║       - 防重复下单机制（数据库持久化）                                   ║
║     • 持仓管理:                                                       ║
║       - 最大持仓数量: 10只                                            ║
║       - 动态止损止盈: 基于ATR自动计算                                  ║
║       - 智能仓位调整: 满仓时自动评估清理弱势持仓                         ║
║     • 资金管理:                                                       ║
║       - 保留最低现金储备: $1,000                                       ║
║       - 资金不足时自动调整仓位大小                                      ║
║                                                                       ║
║  📋 监控列表:                                                          ║
║     • 默认: 从 configs/watchlist.yml 加载                              ║
║     • 内置: 50+个港股 + 8个美股 (使用 --builtin 参数)                   ║
║                                                                       ║
║  🚀 启动命令:                                                          ║
║     python3 scripts/advanced_technical_trading.py                    ║
║     python3 scripts/advanced_technical_trading.py --builtin          ║
║     python3 scripts/advanced_technical_trading.py -n 3               ║
║                                                                       ║
║  命令行参数:                                                           ║
║     --builtin, -b    : 使用内置监控列表                                ║
║     --iterations N, -n N : 限制最大迭代次数 (默认无限循环)              ║
║                                                                       ║
║  按 Ctrl+C 停止                                                       ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
    """)

    asyncio.run(main())