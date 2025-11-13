#!/usr/bin/env python3
"""
信号生成器 - 负责市场分析和信号生成

职责：
1. 获取实时行情数据
2. 计算技术指标（RSI, 布林带, MACD, 成交量等）
3. 评分并生成买入/卖出信号
4. 将信号发送到Redis队列（不执行订单）
5. 检查持仓的止损止盈条件

与原 advanced_technical_trading.py 的区别：
- 移除了订单执行逻辑（execute_signal, submit_order等）
- 信号生成后发送到队列，不直接下单
- 更轻量，专注于市场分析

"""

import asyncio
import sys
from datetime import datetime, timedelta, time
from decimal import Decimal
from zoneinfo import ZoneInfo
from pathlib import Path
from loguru import logger
import numpy as np
from typing import Dict, List, Optional

# 添加项目根目录到路径
sys.path.append(str(Path(__file__).parent.parent))

from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.execution.client import LongportTradingClient
from longport_quant.data.watchlist import WatchlistLoader
from longport_quant.features.technical_indicators import TechnicalIndicators
from longport_quant.messaging import SignalQueue
from longport_quant.utils import LotSizeHelper
from longport_quant.persistence.stop_manager import StopLossManager
from longport_quant.persistence.order_manager import OrderManager
from longport_quant.persistence.position_manager import RedisPositionManager
from longport_quant.risk.regime import RegimeClassifier
from longport_quant.risk.kelly import KellyCalculator
from longport_quant.risk.timezone_capital import TimeZoneCapitalManager
from longport_quant.notifications.notifier import MultiChannelNotifier
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import KlineDaily
from longport_quant.data.kline_sync import KlineDataService
from sqlalchemy import select, and_
from datetime import date


def sanitize_unicode(text: str) -> str:
    """清理无效的Unicode字符"""
    if not text:
        return text
    try:
        return text.encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')
    except Exception:
        return text.encode('ascii', errors='ignore').decode('ascii')


class SignalGenerator:
    """信号生成器（只负责分析和生成信号，不执行订单）"""

    def __init__(self, use_builtin_watchlist=False, max_iterations=None, account_id: str | None = None):
        """
        初始化信号生成器

        Args:
            use_builtin_watchlist: 是否使用内置监控列表
            max_iterations: 最大迭代次数，None表示无限循环
            account_id: 账号ID，如果指定则从configs/accounts/{account_id}.env加载配置
        """
        self.settings = get_settings(account_id=account_id)
        self.account_id = account_id or "default"
        self.beijing_tz = ZoneInfo('Asia/Shanghai')
        self.use_builtin_watchlist = use_builtin_watchlist
        self.max_iterations = max_iterations

        # 初始化消息队列
        self.signal_queue = SignalQueue(
            redis_url=self.settings.redis_url,
            queue_key=self.settings.signal_queue_key,
            processing_key=self.settings.signal_processing_key,
            failed_key=self.settings.signal_failed_key,
            max_retries=self.settings.signal_max_retries
        )

        # 港股监控列表（精选龙头股 + 高科技成长股）
        self.hk_watchlist = {
            # ========================================
            # 龙头股（大市权重/行业龙头）- 16支
            # ========================================

            # === 金融银行（7个）===
            "0005.HK": {"name": "汇丰控股", "sector": "银行"},
            "0939.HK": {"name": "建设银行", "sector": "银行"},
            "1398.HK": {"name": "工商银行", "sector": "银行"},
            "3988.HK": {"name": "中国银行", "sector": "银行"},
            "2318.HK": {"name": "中国平安", "sector": "保险"},
            "1299.HK": {"name": "友邦保险", "sector": "保险"},
            "02378.HK": {"name": "保诚", "sector": "保险"},
            # === 通信（1个）===
            "0941.HK": {"name": "中国移动", "sector": "通信"},

            # === 能源（4个）===
            "0883.HK": {"name": "中国海洋石油", "sector": "能源"},
            "0857.HK": {"name": "中国石油股份", "sector": "能源"},
            "0386.HK": {"name": "中国石化", "sector": "能源"},
            "1088.HK": {"name": "中国神华", "sector": "能源"},

            # === 消费（4个）===
            "9992.HK": {"name": "泡泡玛特", "sector": "消费"},
            "1929.HK": {"name": "周大福", "sector": "消费"},
            "6181.HK": {"name": "老铺黄金", "sector": "消费"},

            # === 地产（1个，可选）===
            "0688.HK": {"name": "中国海外发展", "sector": "地产"},

            # ========================================
            # 高科技成长股 - 18支
            # ========================================

            # === 平台互联网（8个）===
            "0700.HK": {"name": "腾讯控股", "sector": "平台互联网"},
            "9988.HK": {"name": "阿里巴巴-SW", "sector": "平台互联网"},
            "3690.HK": {"name": "美团-W", "sector": "平台互联网"},
            "1810.HK": {"name": "小米集团-W", "sector": "平台互联网"},
            "1024.HK": {"name": "快手-W", "sector": "平台互联网"},

            # === 半导体/光学（6个）===
            "0981.HK": {"name": "中芯国际", "sector": "半导体"},
            "1347.HK": {"name": "华虹半导体", "sector": "半导体"},
            "2382.HK": {"name": "舜宇光学科技", "sector": "光学"},
            "3888.HK": {"name": "金山软件", "sector": "软件"},

            # === 新能源智能车（4个）===
            "1211.HK": {"name": "比亚迪股份", "sector": "新能源汽车"},
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
            # 半导体产业链
            "TSM.US": {"name": "台积电", "sector": "半导体"},
            "ASML.US": {"name": "阿斯麦", "sector": "半导体"},
            # AI & 云计算
            #"PLTR.US": {"name": "Palantir", "sector": "AI"},
            # 电商 & 金融科技
            "SHOP.US": {"name": "Shopify", "sector": "电商"},
            # 杠杆ETF
            "TQQQ.US": {"name": "纳指三倍做多ETF", "sector": "ETF"},
            "NVDU.US": {"name": "英伟达二倍做多ETF", "sector": "ETF"},
            # 其他
            "RKLB.US": {"name": "火箭实验室", "sector": "航天"},
            "RDDT.US": {"name": "reddit", "sector": "reddit"},
            "IREN.US": {"name": "IREN", "sector": "iren"},
            "AVGO.US": {"name": "avgo", "sector": "avgo"},
            "HOOD.US": {"name": "Robinhood", "sector": "金融科技"},
        }

        # A股监控列表
        self.a_watchlist = {
            "300750.SZ": {"name": "宁德时代", "sector": "新能源"},
        }

        # 策略参数
        self.rsi_period = 14
        self.rsi_oversold = 30
        self.rsi_overbought = 70
        self.bb_period = 20
        self.bb_std = 2
        self.macd_fast = 12
        self.macd_slow = 26
        self.macd_signal = 9
        self.volume_surge_threshold = 1.5
        self.use_multi_timeframe = True
        self.use_adaptive_stops = True

        # 轮询间隔
        self.poll_interval = 60  # 60秒扫描一次

        # 信号控制
        self.enable_weak_buy = False  # 禁用WEAK_BUY信号（只生成BUY和STRONG_BUY）
        self.check_market_hours = True  # 启用市场开盘时间检查

        # 止损管理器（用于检查现有持仓）
        self.stop_manager = StopLossManager()
        self.lot_size_helper = LotSizeHelper()

        # 订单管理器（用于检查今日订单，包括pending订单）
        self.order_manager = OrderManager()

        # 【新增】Redis持仓管理器 - 跨进程共享持仓状态
        self.position_manager = RedisPositionManager(
            redis_url=self.settings.redis_url,
            key_prefix="trading"
        )

        # 🔥 市场状态分类器（用于牛熊市判断）
        self.regime_classifier = RegimeClassifier(self.settings)

        # 🎯 凯利公式仓位管理器（智能仓位计算，使用 PostgreSQL）
        self.kelly_calculator = KellyCalculator(
            kelly_fraction=float(getattr(self.settings, 'kelly_fraction', 0.5)),
            max_position_size=float(getattr(self.settings, 'kelly_max_position', 0.25)),
            min_win_rate=float(getattr(self.settings, 'kelly_min_win_rate', 0.55)),
            min_trades=int(getattr(self.settings, 'kelly_min_trades', 10)),
            lookback_days=int(getattr(self.settings, 'kelly_lookback_days', 30))
        )

        # 🌐 时区轮动资金管理器（跨市场资金优化）
        self.timezone_capital_manager = TimeZoneCapitalManager(
            weak_position_threshold=int(getattr(self.settings, 'timezone_weak_threshold', 40)),
            max_rotation_pct=float(getattr(self.settings, 'timezone_max_rotation', 0.30)),
            min_profit_for_rotation=float(getattr(self.settings, 'timezone_min_profit_rotation', -0.10)),
            strong_position_threshold=int(getattr(self.settings, 'timezone_strong_threshold', 70)),
            min_holding_hours=float(getattr(self.settings, 'timezone_min_holding_hours', 0.5))
        )

        # 今日已交易标的集合（避免重复下单）
        self.traded_today = set()  # 今日买单标的（包括pending）
        self.sold_today = set()     # 今日卖单标的（包括pending）- 新增
        self.current_positions = set()  # 当前持仓标的（内存缓存，从Redis同步）

        # 信号生成历史（防止重复信号）
        self.signal_history = {}  # {symbol: last_signal_time}
        # 从配置读取冷却时间，默认900秒（15分钟）
        self.signal_cooldown = int(getattr(self.settings, 'signal_cooldown_seconds', 900))

        # 🚫 防止频繁交易的历史记录（通过Redis共享）
        self.sell_history = {}  # {symbol: last_sell_time} - 用于卖出后再买入冷却期
        self.buy_history = {}   # {symbol: buy_time} - 用于最小持仓时间检查
        self.redis_sell_history_key = f"{self.settings.redis_url.split('//')[-1].split('/')[0]}:trading:sell_history"
        self.redis_buy_history_key = f"{self.settings.redis_url.split('//')[-1].split('/')[0]}:trading:buy_history"

        # 🔥 WebSocket实时订阅相关（事件驱动模式）
        self.websocket_enabled = False  # WebSocket订阅标志
        self.subscribed_symbols = set()  # 已订阅的股票列表
        self.realtime_quotes = {}  # 存储最新实时行情 {symbol: quote}
        self.last_calc_time = {}  # 上次计算时间（防抖）{symbol: timestamp}
        self.indicator_cache = {}  # 技术指标缓存 {symbol: {'price': float, 'indicators': dict}}

        # 🚨 VIXY 恐慌指数实时监控
        self.vixy_symbol = "VIXY.US"
        self.vixy_current_price = None  # VIXY 当前价格
        self.vixy_ma200 = None  # VIXY MA200
        self.market_panic = False  # 市场恐慌标志
        self.last_vixy_alert = None  # 上次恐慌告警时间
        self.vixy_panic_threshold = float(getattr(self.settings, 'vixy_panic_threshold', 30.0))
        self.vixy_alert_enabled = bool(getattr(self.settings, 'vixy_alert_enabled', True))

        # 🔄 港股收盘前强制轮换配置
        self.hk_force_rotation_enabled = bool(getattr(self.settings, 'hk_force_rotation_enabled', False))
        self.hk_force_rotation_max = int(getattr(self.settings, 'hk_force_rotation_max', 2))

        # 🚨 紧急度自动卖出配置
        self.urgent_sell_enabled = bool(getattr(self.settings, 'urgent_sell_enabled', True))
        self.urgent_sell_threshold = int(getattr(self.settings, 'urgent_sell_threshold', 60))
        self.urgent_sell_cooldown = int(getattr(self.settings, 'urgent_sell_cooldown', 300))
        self.urgent_sell_last_check = {}  # {symbol: timestamp} 记录上次检查时间

        # 📊 K线数据混合模式配置（数据库 + API）
        self.use_db_klines = bool(getattr(self.settings, 'use_db_klines', True))
        self.db_klines_history_days = int(getattr(self.settings, 'db_klines_history_days', 90))
        self.api_klines_latest_days = int(getattr(self.settings, 'api_klines_latest_days', 3))

        # 数据库连接管理器（用于K线数据查询）
        self.db = None  # 延迟初始化（在 run() 方法中）
        self.kline_service = None  # K线同步服务（延迟初始化）
        if self.use_db_klines:
            logger.info(
                f"✅ K线混合模式已启用: 数据库{self.db_klines_history_days}天 + API{self.api_klines_latest_days}天"
            )

        # 🔄 实时挪仓和紧急卖出后台任务
        self._rotation_task = None
        self._rotation_check_interval = 30  # 每30秒检查一次

        # 🔔 Slack通知限流（防止429错误）
        self.slack_notification_cooldown = {}  # {notification_key: last_sent_timestamp}
        self.slack_cooldown_period = int(getattr(self.settings, 'slack_cooldown_seconds', 3600))  # 默认1小时

        # 🛡️ 防御性标的（Consumer Staples）- 恐慌期优先监控
        self.defensive_symbols = {
            "PG.US": {"name": "宝洁", "sector": "consumer_staples", "type": "defensive"},
            "KO.US": {"name": "可口可乐", "sector": "consumer_staples", "type": "defensive"},
            "WMT.US": {"name": "沃尔玛", "sector": "consumer_staples", "type": "defensive"},
            "COST.US": {"name": "好市多", "sector": "consumer_staples", "type": "defensive"},
            "MO.US": {"name": "奥驰亚", "sector": "consumer_staples", "type": "defensive"},
        }

        # 🛡️ 恐慌期动态添加的防御标的集合
        self.panic_added_symbols = set()

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

    async def _update_traded_today(self):
        """
        更新今日已下单的标的集合（从orders表查询）

        包括所有有效状态的买单：
        - Filled: 已成交
        - PartialFilled: 部分成交
        - New: 新订单（已提交，等待成交）
        - WaitToNew: 等待提交

        这样可以防止对pending订单重复下单
        """
        try:
            # 使用OrderManager获取今日所有买单标的
            new_traded_today = await self.order_manager.get_today_buy_symbols()

            # 更新成功才赋值
            self.traded_today = new_traded_today

            if self.traded_today:
                logger.info(f"📋 今日已下买单标的: {len(self.traded_today)}个（包括pending订单）")
                logger.debug(f"   详细: {', '.join(sorted(self.traded_today))}")
            else:
                logger.info(f"📋 今日尚无买单记录")

        except Exception as e:
            # 修复：查询失败时保留上一次的值，不清空
            logger.error(f"❌ 更新今日买单失败（保留上次数据）: {e}")
            logger.warning(f"   当前使用的买单列表: {', '.join(sorted(self.traded_today)) if self.traded_today else '空'}")
            import traceback
            logger.debug(f"   错误详情:\n{traceback.format_exc()}")

    async def _update_sold_today(self):
        """
        更新今日已卖出的标的集合（从orders表查询）

        包括所有有效状态的卖单：
        - Filled: 已成交
        - PartialFilled: 部分成交
        - New: 新订单（已提交，等待成交）
        - WaitToNew: 等待提交

        这样可以防止对pending卖单重复生成SELL信号
        """
        try:
            # 使用OrderManager获取今日所有卖单标的
            new_sold_today = await self.order_manager.get_today_sell_symbols()

            # 更新成功才赋值
            self.sold_today = new_sold_today

            if self.sold_today:
                logger.info(f"📋 今日已下卖单标的: {len(self.sold_today)}个（包括pending订单）")
                logger.debug(f"   详细: {', '.join(sorted(self.sold_today))}")
            else:
                logger.info(f"📋 今日尚无卖单记录")

        except Exception as e:
            # 修复：查询失败时保留上一次的值，不清空
            logger.error(f"❌ 更新今日卖单失败（保留上次数据）: {e}")
            logger.warning(f"   当前使用的卖单列表: {', '.join(sorted(self.sold_today)) if self.sold_today else '空'}")
            import traceback
            logger.debug(f"   错误详情:\n{traceback.format_exc()}")

    async def _update_current_positions(self, account: Dict):
        """
        更新当前持仓标的集合（同步到Redis）

        Args:
            account: 账户信息字典
        """
        try:
            positions = account.get("positions", [])

            # 1. 同步到Redis（这是真实的持仓状态）
            await self.position_manager.sync_from_api(positions)

            # 2. 从Redis读取到内存缓存
            self.current_positions = await self.position_manager.get_all_positions()

            if self.current_positions:
                logger.info(f"💼 当前持仓标的: {len(self.current_positions)}个（Redis同步）")
                logger.debug(f"   详细: {', '.join(sorted(self.current_positions))}")
            else:
                logger.info(f"💼 当前无持仓（Redis同步）")

        except Exception as e:
            # 修复：更新失败时从Redis读取（而不是使用旧的内存数据）
            logger.error(f"❌ API持仓更新失败，尝试从Redis读取: {e}")
            try:
                self.current_positions = await self.position_manager.get_all_positions()
                logger.warning(f"   ✅ 已从Redis读取持仓: {len(self.current_positions)}个")
            except Exception as e2:
                logger.error(f"   ❌ Redis读取也失败，保留内存数据: {e2}")
                logger.warning(f"   当前使用的持仓列表: {', '.join(sorted(self.current_positions)) if self.current_positions else '空'}")
            import traceback
            logger.debug(f"   错误详情:\n{traceback.format_exc()}")

    def _is_in_cooldown(self, symbol: str) -> tuple[bool, float]:
        """
        检查标的是否在信号冷却期内

        Args:
            symbol: 标的代码

        Returns:
            (是否在冷却期, 剩余秒数)
        """
        if symbol not in self.signal_history:
            return False, 0

        last_time = self.signal_history[symbol]
        elapsed = (datetime.now(self.beijing_tz) - last_time).total_seconds()
        remaining = self.signal_cooldown - elapsed

        if remaining > 0:
            return True, remaining
        else:
            return False, 0

    async def _is_in_twap_execution(self, symbol: str) -> bool:
        """
        检查标的是否正在进行TWAP订单执行

        Args:
            symbol: 标的代码

        Returns:
            是否在TWAP执行中
        """
        try:
            redis = await self.signal_queue._get_redis()
            redis_key = f"trading:twap_execution:{symbol}"
            result = await redis.get(redis_key)
            return result is not None
        except Exception as e:
            logger.warning(f"检查TWAP执行状态失败: {e}")
            return False

    def _cleanup_signal_history(self):
        """
        清理过期的信号历史记录

        删除1小时前的记录，防止内存泄漏
        """
        now = datetime.now(self.beijing_tz)
        expired = []

        for symbol, last_time in self.signal_history.items():
            if (now - last_time).total_seconds() > 3600:  # 1小时
                expired.append(symbol)

        for symbol in expired:
            del self.signal_history[symbol]

        if expired:
            logger.debug(f"🧹 清理了 {len(expired)} 个过期的信号历史记录")

    async def _should_generate_signal(self, symbol: str, signal_type: str) -> tuple[bool, str]:
        """
        检查是否应该生成信号（多层去重检查）

        Args:
            symbol: 标的代码
            signal_type: 信号类型（BUY/SELL等）

        Returns:
            (bool, str): (是否应该生成, 跳过原因)
        """
        # === 第1层：队列去重 ===
        # 检查队列中是否已有该标的的待处理信号
        if await self.signal_queue.has_pending_signal(symbol, signal_type):
            return False, "队列中已有该标的的待处理信号"

        # === BUY信号的去重与频控检查 ===
        if signal_type in ["BUY", "STRONG_BUY", "WEAK_BUY"]:
            # 全局日度买单上限（可选）
            if getattr(self.settings, 'enable_daily_trade_cap', False):
                try:
                    if len(self.traded_today) >= int(getattr(self.settings, 'daily_max_buy_orders', 9999)):
                        return False, "已达今日买入上限"
                except Exception:
                    pass

            # 单标的日度买单上限（可选，默认1次）
            if getattr(self.settings, 'enable_per_symbol_daily_cap', False):
                try:
                    max_buys = int(getattr(self.settings, 'per_symbol_daily_max_buys', 1))
                    # 使用OrderManager统计该标的今日买单次数（包括待成交）
                    # 为降低DB压力，先用集合快速判断是否已买过一次
                    if max_buys <= 0:
                        return False, "单标的买入次数上限为0"
                    if max_buys == 1 and symbol in self.traded_today:
                        return False, "该标的今日已下过买单"
                except Exception:
                    pass
            # 🔥 修改：移除持仓去重检查，允许对已持仓标的加仓
            # 原因：如果某标的再次出现强买入信号，应该允许加仓（分批建仓策略）

            # TWAP执行检查 - 防止在TWAP订单执行期间生成重复信号
            if await self._is_in_twap_execution(symbol):
                return False, "标的正在进行TWAP订单执行"

            # 🚫 防止频繁交易 - 卖出后再买入冷却期检查
            if self.settings.enable_reentry_cooldown and symbol in self.sell_history:
                last_sell_time = self.sell_history[symbol]
                elapsed = (datetime.now(self.beijing_tz) - last_sell_time).total_seconds()
                if elapsed < self.settings.reentry_cooldown:
                    remaining = self.settings.reentry_cooldown - elapsed
                    logger.info(
                        f"  🚫 {symbol}: 卖出后再买入冷却期内 "
                        f"(已过{elapsed/3600:.1f}小时，还需{remaining/3600:.1f}小时)"
                    )
                    return False, f"卖出后再买入冷却期内（还需{remaining/3600:.1f}小时）"
                else:
                    # 冷却期已过，移除历史记录
                    del self.sell_history[symbol]
                    logger.debug(f"  ✅ {symbol}: 卖出后再买入冷却期已过，允许买入")

            # 时间窗口去重（冷却期检查）- 防止短时间内重复买入
            in_cooldown, remaining = self._is_in_cooldown(symbol)
            if in_cooldown:
                return False, f"信号冷却期内（还需等待{remaining:.0f}秒）"

            # 调试日志：记录允许买入的情况
            has_position = await self.position_manager.has_position(symbol)
            if has_position:
                logger.debug(f"  ✅ {symbol}: 已有持仓，允许加仓")
            elif symbol in self.traded_today:
                logger.debug(f"  ℹ️  {symbol}: 今日已买过但已卖出（或订单未成交），允许再次买入")
            else:
                logger.debug(f"  ℹ️  {symbol}: 今日未买过，允许买入")

        # === SELL信号的去重与频控检查 ===
        elif signal_type in ["SELL", "STOP_LOSS", "TAKE_PROFIT", "SMART_TAKE_PROFIT", "EARLY_TAKE_PROFIT"]:
            # 全局日度卖单上限（止损止盈不受限）
            if signal_type not in ["STOP_LOSS", "TAKE_PROFIT"] and getattr(self.settings, 'enable_daily_trade_cap', False):
                try:
                    if len(self.sold_today) >= int(getattr(self.settings, 'daily_max_sell_orders', 9999)):
                        return False, "已达今日卖出上限"
                except Exception:
                    pass
            # 第2层：检查是否还有持仓（已卖完则不再生成SELL信号）
            if symbol not in self.current_positions:
                return False, "该标的已无持仓"

            # 第3层：今日卖单去重（包括pending订单）
            if symbol in self.sold_today:
                return False, "今日已对该标的下过卖单（包括待成交订单）"

            # 🚫 防止频繁交易 - 最小持仓时间检查（止损止盈豁免）
            if (
                self.settings.enable_min_holding_period
                and symbol in self.buy_history
                and signal_type not in ["STOP_LOSS", "TAKE_PROFIT"]  # 止损止盈不受限制
            ):
                buy_time = self.buy_history[symbol]
                holding_time = (datetime.now(self.beijing_tz) - buy_time).total_seconds()
                if holding_time < self.settings.min_holding_period:
                    remaining = self.settings.min_holding_period - holding_time
                    logger.info(
                        f"  🚫 {symbol}: 持仓时间不足 "
                        f"(已持有{holding_time/60:.0f}分钟，还需{remaining/60:.0f}分钟)"
                    )
                    return False, f"持仓时间不足（还需{remaining/60:.0f}分钟）"

            # 第4层：时间窗口去重
            # 🔥 重要：止损止盈信号不受冷却期限制（必须立即执行）
            if signal_type in ["STOP_LOSS", "TAKE_PROFIT"]:
                # 止损止盈无冷却期，确保实时响应
                logger.debug(f"  ⚡ {symbol}: 止损止盈信号，豁免冷却期检查")
            else:
                # 普通SELL信号检查冷却期
                in_cooldown, remaining = self._is_in_cooldown(symbol)
                if in_cooldown:
                    return False, f"信号冷却期内（还需等待{remaining:.0f}秒）"

        return True, ""

    def _should_send_slack_notification(self, notification_key: str) -> tuple[bool, str]:
        """
        检查是否应该发送Slack通知（限流机制，防止429错误）

        Args:
            notification_key: 通知唯一标识（如 "buying_power:941.HK"）

        Returns:
            (bool, str): (是否应该发送, 跳过原因)
        """
        now_ts = datetime.now(self.beijing_tz).timestamp()

        # 检查是否在冷却期内
        if notification_key in self.slack_notification_cooldown:
            last_sent = self.slack_notification_cooldown[notification_key]
            elapsed = now_ts - last_sent

            if elapsed < self.slack_cooldown_period:
                remaining = self.slack_cooldown_period - elapsed
                remaining_min = remaining / 60
                return False, f"Slack通知冷却期内（还需{remaining_min:.0f}分钟）"

        # 更新发送时间
        self.slack_notification_cooldown[notification_key] = now_ts

        # 清理过期记录（1天前的）
        expired_keys = [
            k for k, v in self.slack_notification_cooldown.items()
            if now_ts - v > 86400  # 24小时
        ]
        for k in expired_keys:
            del self.slack_notification_cooldown[k]

        return True, ""

    # ==================== WebSocket 实时订阅方法 ====================

    async def setup_realtime_subscription(self, symbols):
        """
        设置WebSocket实时订阅，获取推送行情

        优势:
        1. 实时推送，延迟极低（<1秒）
        2. 减少API轮询调用，节省配额
        3. 捕捉最佳买卖点，不错过快速行情
        """
        try:
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

        except Exception as e:
            logger.warning(f"⚠️ WebSocket订阅失败，将使用轮询模式: {e}")
            self.websocket_enabled = False
            self.subscribed_symbols = set()

    def on_realtime_quote(self, symbol, quote):
        """
        实时行情推送回调（同步方法，由LongPort SDK调用）

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
        1. VIXY 恐慌指数监控（特殊处理，不生成买卖信号）
        2. 检查持仓的止损止盈（最高优先级）
        3. 分析新的买入信号（防抖：价格变化>0.5%才计算）
        """
        try:
            current_price = float(quote.last_done)
            if current_price <= 0:
                return

            # 🚨 特殊处理：VIXY 恐慌指数实时监控
            if symbol == self.vixy_symbol:
                await self._handle_vixy_update(current_price)
                return  # VIXY 只监控，不生成买卖信号

            # 防抖：判断是否需要重新计算
            if not self._should_recalculate(symbol, current_price):
                return

            # 🔧 市场时间检查（区分港股和美股）
            session_type = None  # 交易时段类型
            if self.check_market_hours:
                # 美股：支持盘前交易
                if symbol.endswith('.US'):
                    is_premarket, session_type = self._is_us_premarket(symbol)

                    # 盘前时段：如果启用盘前信号，则继续处理
                    if is_premarket:
                        if not getattr(self.settings, 'enable_us_premarket_signals', True):
                            logger.debug(f"  ⏭️  {symbol}: 美股盘前时段，但盘前信号未启用")
                            # 仍检查止损
                            if symbol in self.current_positions:
                                has_position = await self.position_manager.has_position(symbol)
                                if has_position:
                                    await self._check_realtime_stop_loss(symbol, current_price, quote)
                            return
                        # 盘前信号启用，继续处理（session_type = 'pre_market'）
                        logger.debug(f"  🌅 {symbol}: 美股盘前时段，生成盘前信号")

                    # 非常规交易时段：跳过买入信号
                    elif session_type in ['after_hours', 'closed']:
                        logger.debug(f"  ⏭️  {symbol}: 美股非交易时段({session_type})，跳过买入信号分析")
                        if symbol in self.current_positions:
                            has_position = await self.position_manager.has_position(symbol)
                            if has_position:
                                await self._check_realtime_stop_loss(symbol, current_price, quote)
                        return

                # 港股：收盘后跳过买入信号
                elif symbol.endswith('.HK'):
                    if not self._is_market_open(symbol):
                        logger.debug(f"  ⏭️  {symbol}: 港股未开盘，跳过买入信号分析")
                        if symbol in self.current_positions:
                            has_position = await self.position_manager.has_position(symbol)
                            if has_position:
                                await self._check_realtime_stop_loss(symbol, current_price, quote)
                        return

            logger.debug(f"⚡ {symbol}: 价格变化触发实时计算 (${current_price:.2f})")

            # 优先级1：检查持仓的止损止盈（实时检查）
            if symbol in self.current_positions:
                # 从Redis获取最新持仓状态
                has_position = await self.position_manager.has_position(symbol)
                if has_position:
                    # 🔥 实时检查止损止盈（每次价格变化都检查）
                    await self._check_realtime_stop_loss(symbol, current_price, quote)
                    # 🔥 修改：不再直接返回，继续分析买入信号（允许加仓）

            # 优先级2：分析买入信号（包括已持仓标的的加仓信号）
            signal = await self.analyze_symbol_and_generate_signal(symbol, quote, current_price)

            if signal:
                # 去重检查
                should_generate, skip_reason = await self._should_generate_signal(
                    signal['symbol'],
                    signal['type']
                )

                if not should_generate:
                    logger.debug(f"  ⏭️  {symbol}: 跳过信号 - {skip_reason}")
                    return

                # 🌅 盘前信号降权处理
                if session_type == 'pre_market':
                    original_score = signal['score']
                    weight = getattr(self.settings, 'us_premarket_signal_weight', 0.8)
                    signal['score'] = int(original_score * weight)
                    signal['session_type'] = 'pre_market'
                    logger.info(
                        f"  🌅 盘前信号降权: {symbol} 评分 {original_score} → {signal['score']} "
                        f"(权重={weight})"
                    )

                # 发送信号到Redis队列
                success = await self.signal_queue.publish_signal(signal)
                if success:
                    # 记录信号生成时间（用于冷却期检查）
                    self.signal_history[signal['symbol']] = datetime.now(self.beijing_tz)
                    logger.success(
                        f"🔔 {symbol}: 实时信号已生成! 类型={signal['type']}, "
                        f"评分={signal['score']}, 价格=${current_price:.2f}"
                    )

        except Exception as e:
            logger.debug(f"实时处理失败 {symbol}: {e}")

    async def _handle_vixy_update(self, current_price: float):
        """
        处理 VIXY 恐慌指数更新

        功能：
        1. 更新 VIXY 当前价格
        2. 检查是否达到恐慌水平
        3. 发送告警通知
        4. 设置市场恐慌标志

        Args:
            current_price: VIXY 当前价格
        """
        try:
            # 更新当前价格
            self.vixy_current_price = current_price

            # 获取 MA200（首次获取后缓存）
            if self.vixy_ma200 is None:
                self.vixy_ma200 = await self._get_vixy_ma200()

            # 检查恐慌级别
            if current_price > self.vixy_panic_threshold:
                # 达到恐慌水平
                if not self.market_panic:
                    # 首次触发恐慌
                    logger.warning(
                        f"🚨🚨🚨 恐慌指数飙升! VIXY={current_price:.2f} > 阈值{self.vixy_panic_threshold:.2f}"
                    )
                    self.market_panic = True

                    # 🛡️ 激活防御标的监控
                    await self._activate_defensive_watchlist()

                # 发送告警（5分钟内只发一次）
                if self.vixy_alert_enabled:
                    await self._send_vixy_panic_alert(current_price)

                logger.debug(f"🚨 恐慌模式: VIXY={current_price:.2f}, 暂停买入")
            else:
                # 恢复正常
                if self.market_panic:
                    # 从恐慌中恢复
                    logger.info(
                        f"✅ 市场恢复平静: VIXY={current_price:.2f} <= {self.vixy_panic_threshold:.2f}"
                    )
                    self.market_panic = False

                    # 🛡️ 保留防御标的继续监控（推荐）
                    if self.panic_added_symbols:
                        logger.info(
                            f"✅ 保留 {len(self.panic_added_symbols)} 个防御标的继续监控: "
                            f"{', '.join(self.panic_added_symbols)}"
                        )

                ma200_str = f"{self.vixy_ma200:.2f}" if self.vixy_ma200 else "N/A"
                logger.debug(f"📊 VIXY={current_price:.2f}, MA200={ma200_str}")

            # 将 VIXY 状态写入 Redis，供其他组件（如订单执行器）读取
            await self._save_vixy_status_to_redis(current_price)

        except Exception as e:
            logger.error(f"处理 VIXY 更新失败: {e}")

    async def _get_vixy_ma200(self) -> Optional[float]:
        """
        获取 VIXY 的 MA200

        Returns:
            MA200 值，获取失败返回 None
        """
        try:
            # 从 regime_classifier 获取（已经计算过）
            if hasattr(self, 'regime_classifier') and self.regime_classifier:
                # regime_classifier 在 classify() 时会计算 MA200
                # 这里可以直接从最近的 regime 更新中获取
                pass

            # 暂时从行情计算
            bars = await self.quote_client.get_candlesticks(
                self.vixy_symbol,
                period=openapi.Period.Day,
                count=200,
                adjust_type=openapi.AdjustType.NoAdjust
            )

            if bars and len(bars) >= 200:
                closes = [float(bar.close) for bar in bars[-200:]]
                ma200 = sum(closes) / len(closes)
                logger.debug(f"✅ VIXY MA200 计算成功: {ma200:.2f}")
                return ma200
            else:
                logger.warning(f"⚠️  VIXY 历史数据不足 ({len(bars) if bars else 0} bars)")
                return None

        except Exception as e:
            logger.error(f"获取 VIXY MA200 失败: {e}")
            return None

    async def _send_vixy_panic_alert(self, current_price: float):
        """
        发送 VIXY 恐慌告警

        5分钟内只发送一次，避免频繁通知

        Args:
            current_price: VIXY 当前价格
        """
        try:
            now = datetime.now(self.beijing_tz)

            # 检查是否需要发送（5分钟内只发一次）
            if self.last_vixy_alert:
                elapsed = (now - self.last_vixy_alert).total_seconds()
                if elapsed < 300:  # 5分钟 = 300秒
                    logger.debug(f"  ⏭️  恐慌告警冷却中 ({elapsed:.0f}s < 300s)")
                    return

            # 发送告警
            if hasattr(self, 'slack') and self.slack:
                message = (
                    f"🚨 **市场恐慌指数飙升！**\n\n"
                    f"VIXY 当前价格: **${current_price:.2f}**\n"
                    f"恐慌阈值: ${self.vixy_panic_threshold:.2f}\n"
                    f"MA200: {f'${self.vixy_ma200:.2f}' if self.vixy_ma200 else 'N/A'}\n\n"
                    f"⚠️  **已自动停止生成买入信号**\n"
                    f"市场恢复平静后将自动解除"
                )

                await self.slack.send(message)
                logger.success("✅ 恐慌告警已发送")

            # 更新告警时间
            self.last_vixy_alert = now

        except Exception as e:
            logger.error(f"发送恐慌告警失败: {e}")

    async def _save_vixy_status_to_redis(self, current_price: float):
        """
        将 VIXY 状态保存到 Redis，供其他组件读取

        保存的信息：
        - market:vixy:price - 当前价格
        - market:vixy:panic - 是否处于恐慌模式
        - market:vixy:threshold - 恐慌阈值
        - market:vixy:ma200 - MA200 值
        - market:vixy:updated_at - 更新时间

        Args:
            current_price: VIXY 当前价格
        """
        try:
            import redis.asyncio as aioredis
            from datetime import datetime

            redis_client = aioredis.from_url(self.settings.redis_url)

            # 使用 pipeline 批量写入
            pipe = redis_client.pipeline()
            pipe.set("market:vixy:price", str(current_price))
            pipe.set("market:vixy:panic", "1" if self.market_panic else "0")
            pipe.set("market:vixy:threshold", str(self.vixy_panic_threshold))
            pipe.set("market:vixy:ma200", str(self.vixy_ma200) if self.vixy_ma200 else "")
            pipe.set("market:vixy:updated_at", datetime.now(self.beijing_tz).isoformat())

            # 设置过期时间为10分钟（如果信号生成器停止，状态会自动失效）
            pipe.expire("market:vixy:price", 600)
            pipe.expire("market:vixy:panic", 600)
            pipe.expire("market:vixy:threshold", 600)
            pipe.expire("market:vixy:ma200", 600)
            pipe.expire("market:vixy:updated_at", 600)

            await pipe.execute()
            await redis_client.aclose()

            logger.info(f"✅ VIXY 状态已保存: ${current_price:.2f}, 恐慌={self.market_panic}")

        except Exception as e:
            logger.error(f"❌ 保存 VIXY 状态到 Redis 失败: {e}", exc_info=True)

    async def _activate_defensive_watchlist(self):
        """
        激活防御标的监控
        当VIXY触发恐慌时，动态添加防御性标的到监控列表
        """
        try:
            # 找出未订阅的防御标的
            new_symbols = []
            for symbol in self.defensive_symbols.keys():
                if symbol not in self.subscribed_symbols:
                    new_symbols.append(symbol)
                    self.panic_added_symbols.add(symbol)

            if new_symbols:
                logger.success(
                    f"🛡️ **防御模式激活**\n"
                    f"   添加 {len(new_symbols)} 个防御性标的到监控列表:\n"
                    f"   {', '.join(new_symbols)}"
                )

                # WebSocket动态订阅
                if self.websocket_enabled:
                    await self.quote_client.subscribe(
                        symbols=new_symbols,
                        sub_types=[openapi.SubType.Quote],
                        is_first_push=True
                    )

                    self.subscribed_symbols.update(new_symbols)
                    logger.success(f"✅ 成功订阅 {len(new_symbols)} 个防御标的")

                # 发送Slack通知
                if hasattr(self, 'slack') and self.slack:
                    symbol_list = '\n'.join([
                        f"- {s}: {info['name']}"
                        for s, info in self.defensive_symbols.items()
                        if s in new_symbols
                    ])

                    message = (
                        f"🛡️ **防御模式激活**\n\n"
                        f"VIXY: **${self.vixy_current_price:.2f}** > {self.vixy_panic_threshold:.2f}\n\n"
                        f"已添加 {len(new_symbols)} 个防御性标的：\n"
                        f"{symbol_list}\n\n"
                        f"这些标的将在恐慌期继续生成买入信号"
                    )
                    await self.slack.send(message)
            else:
                logger.info("ℹ️ 所有防御标的已在监控列表中")

        except Exception as e:
            logger.error(f"❌ 激活防御监控列表失败: {e}")

    def _should_recalculate(self, symbol: str, current_price: float) -> bool:
        """
        判断是否需要重新计算技术指标（防抖）

        触发条件（满足任一即可）:
        1. 价格变化超过0.5%
        2. 距离上次计算超过5分钟（兜底）
        3. 首次计算

        Returns:
            bool: 是否需要重新计算
        """
        # 条件1：价格变化超过0.5%
        if symbol in self.indicator_cache:
            last_price = self.indicator_cache[symbol]['price']
            price_change_pct = abs(current_price - last_price) / last_price * 100

            if price_change_pct >= 0.5:
                logger.debug(f"  ⚡ {symbol}: 价格变化{price_change_pct:.2f}% (触发阈值0.5%)")
                # 更新缓存
                self.indicator_cache[symbol]['price'] = current_price
                self.last_calc_time[symbol] = datetime.now(self.beijing_tz)
                return True

        # 条件2：距离上次计算超过5分钟（兜底，防止价格变化小但时间久远）
        if symbol in self.last_calc_time:
            elapsed = (datetime.now(self.beijing_tz) - self.last_calc_time[symbol]).total_seconds()
            if elapsed >= 300:  # 5分钟
                logger.debug(f"  ⏰ {symbol}: 距上次计算{elapsed/60:.1f}分钟 (触发阈值5分钟)")
                # 更新缓存
                self.indicator_cache[symbol] = {'price': current_price}
                self.last_calc_time[symbol] = datetime.now(self.beijing_tz)
                return True

        # 条件3：首次计算
        if symbol not in self.indicator_cache:
            logger.debug(f"  🆕 {symbol}: 首次计算")
            self.indicator_cache[symbol] = {'price': current_price}
            self.last_calc_time[symbol] = datetime.now(self.beijing_tz)
            return True

        # 不满足任何条件，跳过计算
        return False

    async def _check_realtime_stop_loss(self, symbol: str, current_price: float, quote):
        """
        实时检查单个持仓的止损止盈（WebSocket实时触发）

        Args:
            symbol: 标的代码
            current_price: 当前价格
            quote: 实时行情对象

        优势：
        - 实时响应（<1秒）
        - 每次价格变化都检查
        - 避免10分钟延迟导致的损失
        """
        try:
            # 1. 获取持仓详情（从Redis）
            position_detail = await self.position_manager.get_position_detail(symbol)
            if not position_detail:
                logger.debug(f"  ℹ️  {symbol}: Redis中无持仓详情")
                return

            cost_price = position_detail.get('cost_price', 0)
            quantity = position_detail.get('quantity', 0)

            # 2. 🔥 混合硬止损检查（-8% + 技术验证）
            # 这是最后防线，防止单日大幅亏损（如PLTR -10%）
            if cost_price > 0:
                profit_pct = (current_price - cost_price) / cost_price

                # 硬止损阈值：-8%
                HARD_STOP_LOSS_PCT = -0.08

                if profit_pct <= HARD_STOP_LOSS_PCT:
                    # 杠杆ETF列表（3x杠杆需要更严格保护）
                    leveraged_keywords = ['TQQQ', 'SQQQ', 'NVDU', 'NVDD', 'LABU', 'LABD',
                                         'TECL', 'TECS', 'UPRO', 'SPXU', 'UDOW', 'SDOW',
                                         'FAS', 'FAZ', 'TNA', 'TZA', 'NAIL', 'DIRV']
                    is_leveraged = any(kw in symbol.upper() for kw in leveraged_keywords)

                    # 技术验证标志
                    technical_confirm = False

                    if is_leveraged:
                        # 杠杆ETF：直接触发，无需技术验证
                        technical_confirm = True
                        technical_reason = "杠杆ETF风险控制"
                    else:
                        # 普通股票：尝试技术验证
                        # 从缓存获取指标（避免实时计算影响性能）
                        cached_data = self.indicator_cache.get(symbol, {})
                        indicators = cached_data.get('indicators', {})

                        if indicators:
                            # 有缓存指标：进行技术验证
                            macd_histogram = indicators.get('macd_histogram', 0)
                            rsi = indicators.get('rsi', 50)
                            sma_20 = indicators.get('sma_20', 0)

                            # 技术弱势信号：
                            # 1. MACD死叉或弱势（柱状图<0）
                            # 2. RSI < 40（弱势区）
                            # 3. 价格跌破MA20
                            macd_weak = macd_histogram < 0
                            rsi_weak = rsi < 40
                            below_ma20 = (sma_20 > 0 and current_price < sma_20)

                            # 任一技术信号确认即触发
                            if macd_weak or rsi_weak or below_ma20:
                                technical_confirm = True
                                signals = []
                                if macd_weak:
                                    signals.append(f"MACD弱势({macd_histogram:.2f})")
                                if rsi_weak:
                                    signals.append(f"RSI弱势({rsi:.1f})")
                                if below_ma20:
                                    signals.append(f"跌破MA20(${sma_20:.2f})")
                                technical_reason = " + ".join(signals)
                            else:
                                # 技术指标未确认，暂不触发
                                logger.info(
                                    f"  ⚠️  {symbol}: 达到-8%但技术指标未确认止损 "
                                    f"(MACD={macd_histogram:.2f}, RSI={rsi:.1f}, "
                                    f"MA20=${sma_20:.2f}, 当前${current_price:.2f})"
                                )
                        else:
                            # 无缓存指标：保护优先，直接触发
                            technical_confirm = True
                            technical_reason = "无技术指标缓存，保护优先"

                    # 触发硬止损
                    if technical_confirm:
                        # 去重检查
                        should_generate, skip_reason = await self._should_generate_signal(symbol, 'HARD_STOP_LOSS')
                        if not should_generate:
                            logger.debug(f"  ⏭️  {symbol}: 跳过硬止损信号 - {skip_reason}")
                        else:
                            loss_pct_abs = abs(profit_pct * 100)
                            entry_time = position_detail.get('entry_time')

                            # 生成硬止损信号
                            signal = {
                                'symbol': symbol,
                                'type': 'HARD_STOP_LOSS',
                                'side': 'SELL',
                                'price': current_price,
                                'quantity': quantity,
                                'reason': f"🚨混合硬止损触发 (亏损{loss_pct_abs:.1f}%, {technical_reason})",
                                'score': 100,  # 最高优先级
                                'timestamp': datetime.now(self.beijing_tz).isoformat(),
                                'priority': 100,
                                'strategy': 'HYBRID_HARD_STOP',
                                'cost_price': cost_price,
                                'entry_time': entry_time,
                                'indicators': {
                                    'current_price': current_price,
                                    'loss_pct': profit_pct * 100,
                                    'technical_reason': technical_reason,
                                    'is_leveraged': is_leveraged,
                                },
                            }

                            success = await self.signal_queue.publish_signal(signal)
                            if success:
                                logger.error(
                                    f"🚨🚨🚨 {symbol}: 混合硬止损触发! "
                                    f"亏损{loss_pct_abs:.1f}% (${cost_price:.2f} → ${current_price:.2f})\n"
                                    f"       原因: {technical_reason}"
                                )
                            return

            # 3. 获取止损止盈设置（从数据库）
            # 注意：account_id 可以为空字符串，stop_manager会处理
            stops = await self.stop_manager.get_position_stops("", symbol)

            if not stops:
                # 没有止损止盈设置，跳过检查
                logger.debug(f"  ℹ️  {symbol}: 无止损止盈设置")
                return

            stop_loss = stops.get('stop_loss')
            take_profit = stops.get('take_profit')

            # 3. 检查固定止损（最高优先级）
            if stop_loss and current_price <= stop_loss:
                loss_pct = (cost_price - current_price) / cost_price * 100

                # 去重检查
                should_generate, skip_reason = await self._should_generate_signal(symbol, 'STOP_LOSS')
                if not should_generate:
                    logger.debug(f"  ⏭️  {symbol}: 跳过止损信号 - {skip_reason}")
                    return

                # 🔥 获取买入时间（用于计算持仓时长）
                entry_time = position_detail.get('entry_time')

                # 生成止损信号
                signal = {
                    'symbol': symbol,
                    'type': 'STOP_LOSS',
                    'side': 'SELL',
                    'price': current_price,
                    'quantity': quantity,
                    'reason': f"实时触发止损 (设置=${stop_loss:.2f}, 亏损{loss_pct:.1f}%)",
                    'score': 100,  # 止损最高优先级
                    'timestamp': datetime.now(self.beijing_tz).isoformat(),
                    'priority': 100,
                    'strategy': 'HYBRID',
                    # 🔥 增强数据：供Slack通知使用
                    'cost_price': cost_price,
                    'entry_time': entry_time,
                    'indicators': {  # 简单记录当前价格信息
                        'current_price': current_price,
                        'stop_loss': stop_loss,
                        'loss_pct': loss_pct,
                    },
                }

                success = await self.signal_queue.publish_signal(signal)
                if success:
                    logger.warning(
                        f"🚨 {symbol}: 实时止损触发! "
                        f"${current_price:.2f} <= ${stop_loss:.2f} "
                        f"(成本${cost_price:.2f}, 亏损{loss_pct:.1f}%)"
                    )
                return

            # 4. 检查固定止盈
            if take_profit and current_price >= take_profit:
                profit_pct = (current_price - cost_price) / cost_price * 100

                # 去重检查
                should_generate, skip_reason = await self._should_generate_signal(symbol, 'TAKE_PROFIT')
                if not should_generate:
                    logger.debug(f"  ⏭️  {symbol}: 跳过止盈信号 - {skip_reason}")
                    return

                # 🔥 获取买入时间（用于计算持仓时长）
                entry_time = position_detail.get('entry_time')

                # 生成止盈信号
                signal = {
                    'symbol': symbol,
                    'type': 'TAKE_PROFIT',
                    'side': 'SELL',
                    'price': current_price,
                    'quantity': quantity,
                    'reason': f"实时触发止盈 (设置=${take_profit:.2f}, 盈利{profit_pct:.1f}%)",
                    'score': 95,
                    'timestamp': datetime.now(self.beijing_tz).isoformat(),
                    'priority': 95,
                    'strategy': 'HYBRID',
                    # 🔥 增强数据：供Slack通知使用
                    'cost_price': cost_price,
                    'entry_time': entry_time,
                    'indicators': {  # 简单记录当前价格信息
                        'current_price': current_price,
                        'take_profit': take_profit,
                        'profit_pct': profit_pct,
                    },
                }

                success = await self.signal_queue.publish_signal(signal)
                if success:
                    logger.success(
                        f"💰 {symbol}: 实时止盈触发! "
                        f"${current_price:.2f} >= ${take_profit:.2f} "
                        f"(成本${cost_price:.2f}, 盈利{profit_pct:.1f}%)"
                    )
                return

            # 5. 未触发任何条件
            stop_loss_str = f"${stop_loss:.2f}" if stop_loss else "N/A"
            take_profit_str = f"${take_profit:.2f}" if take_profit else "N/A"
            logger.debug(
                f"  ℹ️  {symbol}: 价格${current_price:.2f} 在正常范围 "
                f"(止损{stop_loss_str}, 止盈{take_profit_str})"
            )

        except Exception as e:
            logger.debug(f"实时止损止盈检查失败 {symbol}: {e}")

    async def update_subscription_for_positions(self, position_symbols):
        """
        动态更新订阅，确保所有持仓都被监控

        当发现新持仓时，自动加入WebSocket订阅
        """
        if not self.websocket_enabled:
            return  # 如果WebSocket未启用，跳过

        try:
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

                # 🔄 自动同步新持仓的历史K线数据
                await self._auto_sync_position_klines(unsubscribed)

        except Exception as e:
            logger.warning(f"⚠️ 动态订阅失败: {e}")

    async def _auto_sync_position_klines(self, symbols: List[str]):
        """
        自动同步新持仓标的的历史K线数据

        当检测到新持仓时，如果数据库中没有该标的的历史数据，
        自动触发同步，确保后续可以使用混合模式

        Args:
            symbols: 需要检查和同步的标的列表
        """
        if not self.use_db_klines or not self.db or not self.kline_service:
            return  # 混合模式未启用，跳过

        try:
            symbols_to_sync = []

            # 检查每个标的的数据库数据
            for symbol in symbols:
                try:
                    # 检查数据库中是否有该标的的数据
                    end_date = date.today()
                    start_date = end_date - timedelta(days=30)  # 检查最近30天

                    async with self.db.session() as session:
                        stmt = select(KlineDaily).where(
                            and_(
                                KlineDaily.symbol == symbol,
                                KlineDaily.trade_date >= start_date,
                                KlineDaily.trade_date <= end_date
                            )
                        ).limit(1)  # 只需要检查是否存在

                        result = await session.execute(stmt)
                        existing_klines = result.scalar_one_or_none()

                        # 如果数据库中没有数据，标记为需要同步
                        if not existing_klines:
                            symbols_to_sync.append(symbol)
                            logger.info(f"  📊 {symbol}: 数据库无历史数据，将自动同步")
                        else:
                            logger.debug(f"  ✅ {symbol}: 数据库已有数据，跳过同步")

                except Exception as e:
                    logger.debug(f"  ⚠️ {symbol}: 检查数据库失败 - {e}")
                    symbols_to_sync.append(symbol)  # 失败也尝试同步

            # 批量同步需要的标的
            if symbols_to_sync:
                logger.info(f"🔄 开始自动同步 {len(symbols_to_sync)} 个新持仓标的的历史K线...")

                # 计算同步日期范围（同步100天历史）
                sync_end_date = date.today()
                sync_start_date = sync_end_date - timedelta(days=100)

                # 调用同步服务
                results = await self.kline_service.sync_daily_klines(
                    symbols=symbols_to_sync,
                    start_date=sync_start_date,
                    end_date=sync_end_date
                )

                # 统计结果
                success_count = sum(1 for count in results.values() if count > 0)
                total_records = sum(count for count in results.values() if count > 0)

                if success_count > 0:
                    logger.success(
                        f"✅ 自动同步完成: {success_count}/{len(symbols_to_sync)} 个标的，"
                        f"共 {total_records} 条K线记录"
                    )
                else:
                    logger.warning(f"⚠️ K线自动同步未成功，将继续使用API模式")

        except Exception as e:
            logger.warning(f"⚠️ 自动同步K线失败: {e}")
            logger.debug("  系统将自动回退到API模式")

    # ==================== 主循环 ====================

    async def run(self):
        """主循环：扫描市场并生成信号"""
        logger.info("=" * 70)
        logger.info("🚀 信号生成器启动")
        logger.info("=" * 70)

        try:
            # 🔥 连接Redis持仓管理器
            await self.position_manager.connect()
            logger.info("✅ Redis持仓管理器已连接")

            # 📊 初始化数据库连接（用于K线混合模式）
            if self.use_db_klines:
                self.db = DatabaseSessionManager(
                    dsn=self.settings.database_dsn,
                    auto_init=True
                )
                logger.info("✅ 数据库连接已初始化（K线混合模式）")

            # 使用async with正确初始化客户端
            # 初始化通知（支持Slack和Discord）
            slack_url = str(self.settings.slack_webhook_url) if self.settings.slack_webhook_url else None
            discord_url = str(self.settings.discord_webhook_url) if self.settings.discord_webhook_url else None

            async with QuoteDataClient(self.settings) as quote_client, \
                       LongportTradingClient(self.settings) as trade_client, \
                       MultiChannelNotifier(slack_webhook_url=slack_url, discord_webhook_url=discord_url) as slack:

                # 保存客户端引用
                self.quote_client = quote_client
                self.trade_client = trade_client
                self.slack = slack

                # 📊 初始化K线同步服务（用于自动同步新持仓的历史数据）
                if self.use_db_klines and self.db:
                    self.kline_service = KlineDataService(
                        settings=self.settings,
                        db=self.db,
                        quote_client=self.quote_client
                    )
                    logger.info("✅ K线同步服务已初始化")

                # 🔥 保存主事件循环引用（供WebSocket回调使用）
                self._main_loop = asyncio.get_event_loop()

                # 合并所有监控列表
                all_symbols = {}
                if self.use_builtin_watchlist:
                    all_symbols.update(self.hk_watchlist)
                    all_symbols.update(self.us_watchlist)
                    all_symbols.update(self.a_watchlist)
                else:
                    # 从watchlist.yml加载
                    loader = WatchlistLoader(self.settings.watchlist_path)
                    watchlist_data = loader.load_watchlist()
                    all_symbols = {s: {"name": s} for s in watchlist_data.get('symbols', [])}

                # 🚨 添加 VIXY 恐慌指数到监控列表（只监控，不生成买卖信号）
                all_symbols[self.vixy_symbol] = {
                    "name": "VIXY恐慌指数ETF",
                    "type": "RISK_INDICATOR"
                }

                logger.info(f"📋 监控标的数量: {len(all_symbols)} (含 VIXY 恐慌指数)")
                logger.info(f"⏰ 轮询间隔: {self.poll_interval}秒")
                logger.info(f"📤 信号队列: {self.settings.signal_queue_key}")
                logger.info("")

                # 🔥 设置WebSocket实时订阅（事件驱动模式）
                symbols_list = list(all_symbols.keys())
                await self.setup_realtime_subscription(symbols_list)

                # 根据WebSocket是否启用调整轮询间隔
                if self.websocket_enabled:
                    # WebSocket模式：降低轮询频率到10分钟（只用于状态同步）
                    actual_poll_interval = 600
                    logger.info("   🎯 模式: WebSocket实时推送 + 10分钟定期同步")
                else:
                    # 轮询模式：保持60秒间隔
                    actual_poll_interval = self.poll_interval
                    logger.info("   🎯 模式: 60秒轮询扫描")

                # 🔄 启动实时挪仓和紧急卖出后台任务
                self._rotation_task = asyncio.create_task(self._rotation_checker_loop())
                logger.info("✅ 实时挪仓后台任务已启动（独立于主循环，每30秒检查）")

                iteration = 0
                while True:
                    if self.max_iterations and iteration >= self.max_iterations:
                        logger.info(f"✅ 达到最大迭代次数 {self.max_iterations}，退出")
                        break

                    iteration += 1
                    logger.info(f"\n{'='*70}")
                    logger.info(f"🔄 第 {iteration} 轮扫描开始 ({datetime.now(self.beijing_tz).strftime('%Y-%m-%d %H:%M:%S')})")
                    logger.info(f"{'='*70}")

                    try:
                        # 1. 更新今日已交易标的和当前持仓
                        logger.debug(f"📊 开始更新去重数据...")
                        await self._update_traded_today()  # 更新买单
                        await self._update_sold_today()    # 更新卖单
                        try:
                            account = await self.trade_client.get_account()
                            await self._update_current_positions(account)

                            # 🔥 动态更新WebSocket订阅（确保所有持仓都被监控）
                            if account and account.get("positions"):
                                # positions 是列表，每个元素是 {"symbol": "857.HK", ...}
                                position_symbols = [pos["symbol"] for pos in account["positions"] if "symbol" in pos]
                                if position_symbols:
                                    await self.update_subscription_for_positions(position_symbols)

                        except Exception as e:
                            logger.warning(f"⚠️ 获取账户信息失败: {e}")
                            logger.debug(f"   使用上一次的持仓数据: {', '.join(sorted(self.current_positions)) if self.current_positions else '空'}")
                            account = None

                        # 汇总去重状态
                        logger.info(f"📋 去重数据汇总: 持仓{len(self.current_positions)}个, 今日买过{len(self.traded_today)}个, 今日卖过{len(self.sold_today)}个")

                        # 2. 定期清理信号历史（每10轮一次，防止内存泄漏）
                        if iteration % 10 == 0:
                            self._cleanup_signal_history()

                        # 3. 获取实时行情
                        symbols = list(all_symbols.keys())
                        quotes = await self.quote_client.get_realtime_quote(symbols)

                        if not quotes:
                            logger.warning("⚠️ 未获取到行情数据")
                            await asyncio.sleep(actual_poll_interval)
                            continue

                        logger.info(f"📊 获取到 {len(quotes)} 个标的的实时行情")

                        # 4. 分析每个标的并生成信号
                        # 🔥 如果WebSocket已启用，跳过轮询扫描信号生成（信号由实时推送触发）
                        if self.websocket_enabled:
                            logger.debug("   ⏭️  WebSocket模式：跳过轮询扫描信号生成（实时推送中）")
                            signals_generated = 0
                        else:
                            # 轮询模式：逐个分析标的并生成信号
                            signals_generated = 0
                            for quote in quotes:
                                try:
                                    symbol = quote.symbol
                                    current_price = float(quote.last_done)

                                    logger.info(f"\n📊 分析 {symbol} ({all_symbols.get(symbol, {}).get('name', symbol)})")
                                    logger.info(f"  实时行情: 价格=${current_price:.2f}, 成交量={quote.volume:,}")

                                    # 检查市场是否开盘
                                    if self.check_market_hours and not self._is_market_open(symbol):
                                        logger.debug(f"  ⏭️  跳过 {symbol} (市场未开盘)")
                                        continue

                                    # 分析标的并生成信号
                                    signal = await self.analyze_symbol_and_generate_signal(symbol, quote, current_price)

                                    if signal:
                                        # 检查是否应该生成信号（去重检查）
                                        should_generate, skip_reason = await self._should_generate_signal(
                                            signal['symbol'],
                                            signal['type']
                                        )

                                        if not should_generate:
                                            logger.info(f"  ⏭️  跳过信号: {skip_reason}")
                                            continue
                                        # 发送信号到队列
                                        success = await self.signal_queue.publish_signal(signal)
                                        if success:
                                            signals_generated += 1
                                            # 记录信号生成时间（用于冷却期检查）
                                            self.signal_history[signal['symbol']] = datetime.now(self.beijing_tz)
                                            logger.success(
                                                f"  ✅ 信号已发送到队列: {signal['type']}, "
                                                f"评分={signal['score']}, 优先级={signal.get('priority', signal['score'])}"
                                            )
                                        else:
                                            logger.error(f"  ❌ 信号发送失败: {symbol}")

                                except Exception as e:
                                    logger.error(f"  ❌ 分析标的失败 {symbol}: {e}")
                                    continue

                        # 5. 🔥 获取当前市场状态（牛熊市判断）
                        try:
                            regime_result = await self.regime_classifier.classify(
                                quote=self.quote_client,
                                filter_by_market=True
                            )
                            regime = regime_result.regime
                            logger.info(f"📈 市场状态: {regime} - {regime_result.details}")
                        except Exception as e:
                            logger.warning(f"⚠️ 市场状态检测失败: {e}，使用默认值RANGE")
                            regime = "RANGE"

                        # 6. 🔄 收盘前自动轮换检查（时区资金优化）
                        rotation_signals = []
                        try:
                            if account and getattr(self.settings, 'timezone_rotation_enabled', True):
                                rotation_signals = await self.check_pre_close_rotation(quotes, account, regime)

                                # 发送轮换信号到队列
                                for rotation_signal in rotation_signals:
                                    success = await self.signal_queue.publish_signal(rotation_signal)
                                    if success:
                                        logger.success(
                                            f"  ✅ 轮换信号已发送: {rotation_signal['symbol']}, "
                                            f"评分={rotation_signal['score']}"
                                        )
                                    else:
                                        logger.error(f"  ❌ 轮换信号发送失败: {rotation_signal['symbol']}")
                        except Exception as e:
                            logger.error(f"⚠️ 收盘前轮换检查失败: {e}")

                        # 6.5. 🔄 实时挪仓检查 - 已移至后台任务（每30秒独立检查）
                        # 实时挪仓和紧急卖出现在由 _rotation_checker_loop() 后台任务处理
                        # 这样可以更快速地响应资金不足的情况，不受主循环间隔限制
                        pass

                        # 6.6. 🚨 紧急度自动卖出检查 - 已移至后台任务（每30秒独立检查）
                        pass

                        # 7. 检查现有持仓的止损止盈（生成平仓信号）
                        try:
                            if account:
                                exit_signals = await self.check_exit_signals(quotes, account, regime)
                            else:
                                exit_signals = []

                            for exit_signal in exit_signals:
                                # 检查是否应该生成信号（去重检查）- 修复：exit信号也需要去重
                                should_generate, skip_reason = await self._should_generate_signal(
                                    exit_signal['symbol'],
                                    exit_signal['type']
                                )

                                if not should_generate:
                                    logger.info(f"  ⏭️  跳过平仓信号 ({exit_signal['symbol']}): {skip_reason}")
                                    continue

                                success = await self.signal_queue.publish_signal(exit_signal)
                                if success:
                                    signals_generated += 1
                                    # 记录信号生成时间（用于冷却期检查）
                                    self.signal_history[exit_signal['symbol']] = datetime.now(self.beijing_tz)
                                    logger.success(
                                        f"  ✅ 平仓信号已发送: {exit_signal['symbol']}, "
                                        f"原因={exit_signal.get('reason', 'N/A')}"
                                    )
                        except Exception as e:
                            logger.warning(f"⚠️ 检查止损止盈失败: {e}")

                        # 7. 🔥 检查加仓机会（智能加仓）
                        try:
                            if account:
                                add_signals = await self.check_add_position_signals(quotes, account, regime)
                            else:
                                add_signals = []

                            for add_signal in add_signals:
                                # 检查是否应该生成信号（去重检查）
                                should_generate, skip_reason = await self._should_generate_signal(
                                    add_signal['symbol'],
                                    add_signal['type']
                                )

                                if not should_generate:
                                    logger.info(f"  ⏭️  跳过加仓信号 ({add_signal['symbol']}): {skip_reason}")
                                    continue

                                success = await self.signal_queue.publish_signal(add_signal)
                                if success:
                                    signals_generated += 1
                                    # 记录信号生成时间（用于冷却期检查）
                                    self.signal_history[add_signal['symbol']] = datetime.now(self.beijing_tz)
                                    logger.success(
                                        f"  ✅ 加仓信号已发送: {add_signal['symbol']}, "
                                        f"数量={add_signal.get('quantity', 0)}"
                                    )
                        except Exception as e:
                            logger.warning(f"⚠️ 检查加仓机会失败: {e}")

                        # 8. 显示本轮统计
                        queue_stats = await self.signal_queue.get_stats()
                        logger.info(f"\n📊 本轮统计:")
                        logger.info(f"  新生成信号: {signals_generated}")
                        logger.info(f"  队列待处理: {queue_stats['queue_size']}")
                        logger.info(f"  正在处理: {queue_stats['processing_size']}")
                        logger.info(f"  失败队列: {queue_stats['failed_size']}")

                    except Exception as e:
                        logger.error(f"❌ 扫描循环出错: {e}")
                        import traceback
                        logger.debug(traceback.format_exc())

                    # 等待下一轮
                    if self.websocket_enabled:
                        logger.info(f"\n💤 等待 {actual_poll_interval} 秒后进行状态同步...")
                        logger.info("   （WebSocket实时接收行情，信号即时生成）")
                    else:
                        logger.info(f"\n💤 等待 {actual_poll_interval} 秒后进行下一轮扫描...")
                    await asyncio.sleep(actual_poll_interval)

        except KeyboardInterrupt:
            logger.info("\n⚠️ 收到中断信号，正在退出...")
        finally:
            # 取消后台任务
            if self._rotation_task and not self._rotation_task.done():
                logger.info("🛑 停止实时挪仓后台任务...")
                self._rotation_task.cancel()
                try:
                    await self._rotation_task
                except asyncio.CancelledError:
                    pass
                logger.info("✅ 实时挪仓后台任务已停止")

            # 关闭Redis连接
            await self.signal_queue.close()
            await self.position_manager.close()
            logger.info("✅ 资源清理完成")

    async def analyze_symbol_and_generate_signal(
        self,
        symbol: str,
        quote,
        current_price: float
    ) -> Optional[Dict]:
        """
        分析标的并生成信号

        Returns:
            Dict: 信号数据，如果不生成信号则返回None
        """
        try:
            # 🚨 恐慌断路器：市场恐慌时的分级响应
            if self.market_panic:
                # 检查是否为防御性标的
                is_defensive = symbol in self.defensive_symbols

                if is_defensive:
                    logger.info(
                        f"🛡️ {symbol}: 防御性标的，恐慌期间继续监控 "
                        f"(VIXY={self.vixy_current_price:.2f})"
                    )
                    # 继续执行信号生成逻辑
                else:
                    logger.warning(
                        f"🚨 {symbol}: 市场恐慌 (VIXY={self.vixy_current_price:.2f}), "
                        f"暂停买入信号生成"
                    )
                    return None

            # 获取历史K线数据
            end_date = datetime.now()
            days_to_fetch = 100  # 获取更多数据以确保有足够的历史
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
                return None

            if not candles or len(candles) < 30:
                logger.warning(
                    f"  ❌ 历史数据不足，跳过分析\n"
                    f"     实际: {len(candles) if candles else 0}天\n"
                    f"     需要: 至少30天"
                )
                return None

            # 提取价格数据
            closes = np.array([float(c.close) for c in candles])
            highs = np.array([float(c.high) for c in candles])
            lows = np.array([float(c.low) for c in candles])
            volumes = np.array([c.volume for c in candles])

            # 计算技术指标
            logger.debug(f"  🔬 开始计算技术指标 (数据长度: {len(closes)}天)...")
            indicators = self._calculate_all_indicators(closes, highs, lows, volumes)
            logger.debug(f"  ✅ 技术指标计算完成")

            # 分析买入信号
            signal = self._analyze_buy_signals(symbol, current_price, quote, indicators, closes, highs, lows)

            # 🔥 买入前预检查：如果是买入信号，检查可买数量
            if signal and signal.get('type') in ['BUY', 'WEAK_BUY']:
                signal_score = signal.get('score', 0)
                can_buy, analysis_msg = await self._check_buying_power_before_signal(
                    symbol=symbol,
                    current_price=current_price,
                    signal_score=signal_score,
                    signal=signal
                )

                if not can_buy:
                    # 预检查失败，不生成买入信号
                    logger.warning(f"  ⏭️  {symbol}: 预检查失败，跳过买入信号生成")
                    return None

            return signal

        except Exception as e:
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
                logger.error(
                    f"  ❌ 分析失败: {symbol}\n"
                    f"     错误类型: {error_type}\n"
                    f"     错误信息: {error_msg}"
                )
                import traceback
                logger.debug(f"     堆栈跟踪:\n{traceback.format_exc()}")

            return None

    def _calculate_all_indicators(self, closes, highs, lows, volumes):
        """计算所有技术指标"""
        try:
            # RSI
            rsi = TechnicalIndicators.rsi(closes, self.rsi_period)

            # 布林带
            bb = TechnicalIndicators.bollinger_bands(closes, self.bb_period, self.bb_std)

            # MACD
            macd_result = TechnicalIndicators.macd(
                closes, self.macd_fast, self.macd_slow, self.macd_signal
            )

            # 均线
            sma_20 = TechnicalIndicators.sma(closes, 20) if self.use_multi_timeframe else None
            sma_50 = TechnicalIndicators.sma(closes, 50) if self.use_multi_timeframe else None

            # 成交量均线
            volume_sma = TechnicalIndicators.sma(volumes, 20)

            # ATR (用于动态止损)
            atr = TechnicalIndicators.atr(highs, lows, closes, 14) if self.use_adaptive_stops else None

            return {
                'rsi': rsi[-1] if len(rsi) > 0 else np.nan,
                'bb_upper': bb['upper'][-1] if len(bb['upper']) > 0 else np.nan,
                'bb_middle': bb['middle'][-1] if len(bb['middle']) > 0 else np.nan,
                'bb_lower': bb['lower'][-1] if len(bb['lower']) > 0 else np.nan,
                'macd': macd_result['macd'][-1] if len(macd_result['macd']) > 0 else np.nan,
                'macd_line': macd_result['macd'][-1] if len(macd_result['macd']) > 0 else np.nan,  # 🔥 MACD线（用于0轴检测）
                'prev_macd_line': macd_result['macd'][-2] if len(macd_result['macd']) > 1 else 0,  # 🔥 前一个MACD线
                'macd_signal': macd_result['signal'][-1] if len(macd_result['signal']) > 0 else np.nan,
                'macd_histogram': macd_result['histogram'][-1] if len(macd_result['histogram']) > 0 else np.nan,
                'prev_macd_histogram': macd_result['histogram'][-2] if len(macd_result['histogram']) > 1 else 0,
                'sma_20': sma_20[-1] if sma_20 is not None and len(sma_20) > 0 else np.nan,
                'sma_50': sma_50[-1] if sma_50 is not None and len(sma_50) > 0 else np.nan,
                'volume_sma': volume_sma[-1] if len(volume_sma) > 0 else np.nan,
                'atr': atr[-1] if atr is not None and len(atr) > 0 else np.nan,
            }

        except Exception as e:
            logger.error(
                f"计算技术指标失败:\n"
                f"  错误类型: {type(e).__name__}\n"
                f"  错误信息: {e}\n"
                f"  数据长度: closes={len(closes)}, highs={len(highs)}, "
                f"lows={len(lows)}, volumes={len(volumes)}"
            )
            import traceback
            logger.debug(f"  堆栈跟踪:\n{traceback.format_exc()}")

            # 返回空指标
            return {
                'rsi': np.nan, 'bb_upper': np.nan, 'bb_middle': np.nan, 'bb_lower': np.nan,
                'macd': np.nan, 'macd_line': np.nan, 'prev_macd_line': 0,
                'macd_signal': np.nan, 'macd_histogram': np.nan,
                'prev_macd_histogram': 0, 'sma_20': np.nan, 'sma_50': np.nan,
                'volume_sma': np.nan, 'atr': np.nan,
            }

    def _analyze_buy_signals(self, symbol, current_price, quote, ind, closes, highs, lows):
        """
        综合分析买入信号（混合策略：逆向 + 趋势跟随）

        评分系统:
        - RSI: 0-30分 (超卖或强势区间)
        - 布林带: 0-25分 (接近下轨或突破上轨)
        - MACD: 0-20分 (金叉信号)
        - 成交量: 0-15分 (放量确认)
        - 趋势: 0-10分 (均线方向)
        总分: 0-100分

        阈值:
        - >= 60分: 强买入信号
        - >= 45分: 买入信号
        - >= 30分: 弱买入信号
        """
        score = 0
        reasons = []

        # 计算成交量比率
        current_volume = quote.volume if quote.volume else 0
        if ind['volume_sma'] and ind['volume_sma'] > 0:
            volume_ratio = float(current_volume) / float(ind['volume_sma'])
        else:
            volume_ratio = 1.0

        logger.debug(f"    成交量计算: 当前={current_volume:,}, 平均={ind.get('volume_sma', 0):,.0f}, 比率={volume_ratio:.2f}")

        # 计算布林带位置
        bb_range = ind['bb_upper'] - ind['bb_lower']
        if bb_range > 0:
            bb_position_pct = (current_price - ind['bb_lower']) / bb_range * 100
        else:
            bb_position_pct = 50

        bb_width_pct = bb_range / ind['bb_middle'] * 100 if ind['bb_middle'] > 0 else 0

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
        elif ind['rsi'] < 40:
            rsi_score = 15
            rsi_reason = f"偏低({ind['rsi']:.1f})"
            reasons.append(f"RSI{rsi_reason}")
        elif 40 <= ind['rsi'] <= 50:
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
        elif current_price <= ind['bb_lower'] * 1.02:  # 接近下轨
            bb_score = 20
            bb_reason = "接近下轨"
            reasons.append(f"接近布林带下轨")
        elif bb_position_pct < 30:  # 下半部
            bb_score = 10
            bb_reason = f"下半部({bb_position_pct:.0f}%)"
            reasons.append(f"布林带下半部")
        elif current_price >= ind['bb_upper']:  # 突破上轨（趋势跟随策略）
            bb_score = 20
            bb_reason = f"突破上轨(${ind['bb_upper']:.2f})"
            reasons.append(f"突破布林带上轨(${ind['bb_upper']:.2f})")
        elif current_price >= ind['bb_upper'] * 0.98:  # 接近上轨
            bb_score = 15
            bb_reason = "接近上轨"
            reasons.append(f"接近布林带上轨")
        else:
            bb_reason = f"位置{bb_position_pct:.0f}%"

        # 布林带收窄加分
        if bb_width_pct < 10:
            bb_score += 5
            bb_reason += " + 极度收窄"
        elif bb_width_pct < 15:
            bb_score += 3
            bb_reason += " + 收窄"

        logger.info(f"    布林带得分: {bb_score}/25 ({bb_reason})")
        score += bb_score

        # === 3. MACD分析 (0-20分) ===
        macd_score = 0
        macd_reason = ""
        if ind['prev_macd_histogram'] < 0 and ind['macd_histogram'] > 0:
            macd_score = 20
            macd_reason = "金叉"
            reasons.append("MACD金叉")
        elif ind['macd_histogram'] > 0 and ind['macd'] > ind['macd_signal']:
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
        if volume_ratio >= 2.0:
            volume_score = 15
            vol_reason = f"大幅放量({volume_ratio:.1f}x)"
            reasons.append(f"成交量大幅放大({volume_ratio:.1f}x)")
        elif volume_ratio >= self.volume_surge_threshold:
            volume_score = 10
            vol_reason = f"放量({volume_ratio:.1f}x)"
            reasons.append(f"成交量放大({volume_ratio:.1f}x)")
        elif volume_ratio >= 1.2:
            volume_score = 5
            vol_reason = f"温和放量({volume_ratio:.1f}x)"
            reasons.append(f"成交量温和({volume_ratio:.1f}x)")
        elif volume_ratio >= 0.8:  # 正常成交量（趋势跟随场景）
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
            if current_price > ind['sma_20']:
                trend_score += 3
                reasons.append("价格在SMA20上方")

            if ind['sma_20'] > ind['sma_50']:
                trend_score += 7
                trend_reason = "上升趋势"
                reasons.append("SMA20在SMA50上方(上升趋势)")
            elif ind['sma_20'] > ind['sma_50'] * 0.98:
                trend_score += 4
                trend_reason = "接近金叉"
                reasons.append("接近均线金叉")
            else:
                trend_reason = "下降趋势或中性"
        else:
            trend_score = 5
            trend_reason = "未启用多时间框架"

        logger.info(f"    趋势得分: {trend_score}/10 ({trend_reason})")
        score += trend_score

        # 🚫 防止频繁交易 - 交易成本惩罚（降低频繁交易动机）
        original_score = score
        if self.settings.enable_transaction_cost_penalty:
            # 将交易成本（百分比）转换为评分扣减（假设满分100对应10%的收益潜力）
            # 例如：0.2%交易成本 = 2分扣减（0.2% / 10% * 100 = 2）
            cost_penalty = int(self.settings.transaction_cost_pct * 1000)  # 0.002 * 1000 = 2
            score = max(0, score - cost_penalty)
            logger.info(f"    💰 交易成本惩罚: -{cost_penalty}分 (成本比例: {self.settings.transaction_cost_pct*100:.2f}%)")

        # 🛡️ 防御标的恐慌期加分
        if self.market_panic and symbol in self.defensive_symbols:
            defensive_bonus = 15  # 恐慌期给予15分额外加分
            score += defensive_bonus
            reasons.append("🛡️ 防御标的恐慌期加分")
            logger.info(f"    🛡️ 防御标的恐慌期加分: +{defensive_bonus}分 (VIXY={self.vixy_current_price:.2f})")

        # 总分和决策
        logger.info(
            f"\n  📈 综合评分: {score}/100"
            + (f" (原始分: {original_score})" if self.settings.enable_transaction_cost_penalty else "")
        )

        # 判断是否生成信号
        if score >= 30:  # 弱买入以上
            signal_type = "STRONG_BUY" if score >= 60 else ("BUY" if score >= 45 else "WEAK_BUY")
            signal_strength = score / 100.0

            # 检查是否禁用WEAK_BUY信号
            if signal_type == "WEAK_BUY" and not self.enable_weak_buy:
                logger.info(f"  ⏭️  不生成WEAK_BUY信号 (已禁用，得分={score})")
                return None

            # 计算止损止盈（根据信号强度动态调整止损距离）
            atr = ind.get('atr', 0)
            if atr and atr > 0:
                # 根据信号强度调整止损距离倍数
                if score >= 80:
                    stop_multiplier = 2.0  # 极强信号：更紧止损
                elif score >= 60:
                    stop_multiplier = 2.5  # 强信号：标准止损
                else:
                    stop_multiplier = 3.0  # 一般信号：宽松止损

                stop_loss = current_price - (stop_multiplier * atr)
                take_profit = current_price + (3.5 * atr)
            else:
                # 无ATR时使用固定百分比
                if score >= 80:
                    stop_loss = current_price * 0.96  # -4%
                elif score >= 60:
                    stop_loss = current_price * 0.95  # -5%
                else:
                    stop_loss = current_price * 0.93  # -7%
                take_profit = current_price * 1.10

            logger.success(
                f"  ✅ 决策: 生成买入信号 (得分{score} >= 30)\n"
                f"     信号类型: {signal_type}\n"
                f"     强度: {signal_strength:.2f}\n"
                f"     原因: {', '.join(reasons)}"
            )

            # 构造信号数据（发送到队列）
            signal = {
                'symbol': symbol,
                'type': signal_type,
                'side': 'BUY',
                'score': score,
                'strength': signal_strength,
                'price': current_price,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'reasons': reasons,
                'strategy': 'HYBRID',
                'indicators': {
                    'rsi': float(ind['rsi']),
                    'bb_upper': float(ind['bb_upper']),
                    'bb_middle': float(ind['bb_middle']),
                    'bb_lower': float(ind['bb_lower']),
                    'macd': float(ind['macd']),
                    'macd_signal': float(ind['macd_signal']),
                    'volume_ratio': float(volume_ratio),
                    'sma_20': float(ind['sma_20']) if not np.isnan(ind['sma_20']) else None,
                    'sma_50': float(ind['sma_50']) if not np.isnan(ind['sma_50']) else None,
                    'atr': float(ind['atr']) if not np.isnan(ind['atr']) else None,
                },
                'timestamp': datetime.now(self.beijing_tz).isoformat(),
                'priority': score,  # 用于队列排序
            }

            return signal

        else:
            logger.info(f"  ⏭️  不生成信号 (得分{score} < 30)")
            return None

    async def _load_klines_from_db(self, symbol: str, days: int = 90) -> List[KlineDaily]:
        """
        从数据库加载历史K线数据

        Args:
            symbol: 标的代码
            days: 需要的天数

        Returns:
            K线列表（按日期升序）
        """
        try:
            from datetime import date as datetime_date

            end_date = datetime_date.today()
            start_date = end_date - timedelta(days=days)

            async with self.db.session() as session:
                stmt = select(KlineDaily).where(
                    and_(
                        KlineDaily.symbol == symbol,
                        KlineDaily.trade_date >= start_date,
                        KlineDaily.trade_date <= end_date
                    )
                ).order_by(KlineDaily.trade_date.asc())  # 升序，与API一致

                result = await session.execute(stmt)
                klines = result.scalars().all()

                logger.debug(
                    f"  📊 {symbol}: 从数据库读取 {len(klines)} 根K线 "
                    f"({start_date} ~ {end_date})"
                )
                return list(klines)

        except Exception as e:
            logger.warning(f"  ⚠️ {symbol}: 数据库查询失败 - {e}")
            return []

    def _merge_klines(self, db_klines: List[KlineDaily], api_candles: List) -> List:
        """
        合并数据库K线和API K线，去重

        逻辑：
        1. 按日期去重（API数据优先，因为更准确）
        2. 按日期升序排序
        3. 返回统一格式

        Args:
            db_klines: 数据库K线列表
            api_candles: API K线列表

        Returns:
            合并后的K线列表（统一为API格式）
        """
        try:
            from datetime import date as datetime_date

            # 转换数据库K线为字典 {date: kline}
            db_dict = {}
            for kline in db_klines:
                db_dict[kline.trade_date] = kline

            # 转换API K线为字典（API数据优先）
            api_dict = {}
            for candle in (api_candles or []):
                # API candle 通常有 timestamp 属性
                if hasattr(candle, 'timestamp'):
                    trade_date = candle.timestamp.date()
                elif hasattr(candle, 'date'):
                    trade_date = candle.date if isinstance(candle.date, datetime_date) else candle.date.date()
                else:
                    continue
                api_dict[trade_date] = candle

            # 创建统一格式的K线列表
            # 需要将数据库K线转换为类似API candle的格式
            class CandleWrapper:
                """包装数据库K线，使其接口与API一致"""
                def __init__(self, kline):
                    self.close = kline.close
                    self.open = kline.open
                    self.high = kline.high
                    self.low = kline.low
                    self.volume = kline.volume
                    self.timestamp = datetime.combine(kline.trade_date, datetime.min.time())
                    self.trade_date = kline.trade_date

            # 合并（先用数据库数据，再用API数据覆盖）
            all_dates = set(db_dict.keys()) | set(api_dict.keys())

            merged_list = []
            for trade_date in sorted(all_dates):
                if trade_date in api_dict:
                    # API数据优先
                    merged_list.append(api_dict[trade_date])
                elif trade_date in db_dict:
                    # 使用数据库数据（包装为API格式）
                    merged_list.append(CandleWrapper(db_dict[trade_date]))

            logger.debug(
                f"  🔗 合并K线: 数据库{len(db_klines)}根 + API{len(api_candles or [])}根 "
                f"→ 总计{len(merged_list)}根（去重后）"
            )

            return merged_list

        except Exception as e:
            logger.error(f"  ❌ K线合并失败: {e}")
            # 回退：只返回API数据
            return api_candles or []

    async def _fetch_current_indicators(self, symbol: str, quote) -> Optional[Dict]:
        """
        获取标的当前的技术指标（用于退出决策）

        Args:
            symbol: 标的代码
            quote: 实时行情数据

        Returns:
            指标字典，如果获取失败返回None
        """
        try:
            # 🔥 混合模式：数据库 + API
            candles = []

            if self.use_db_klines and self.db:
                # 1️⃣ 从数据库获取历史数据
                db_klines = await self._load_klines_from_db(
                    symbol=symbol,
                    days=self.db_klines_history_days
                )

                # 2️⃣ 从API获取最新数据
                end_date = datetime.now()
                start_date = end_date - timedelta(days=self.api_klines_latest_days)

                api_candles = await self.quote_client.get_history_candles(
                    symbol=symbol,
                    period=openapi.Period.Day,
                    adjust_type=openapi.AdjustType.NoAdjust,
                    start=start_date,
                    end=end_date
                )

                # 3️⃣ 合并数据
                if db_klines and len(db_klines) >= 30:
                    # 数据库数据充足，使用混合模式
                    candles = self._merge_klines(db_klines, api_candles)
                    logger.debug(
                        f"  ✅ {symbol}: 混合模式 - "
                        f"数据库{len(db_klines)}根 + API{len(api_candles or [])}根"
                    )
                else:
                    # 数据库数据不足，回退到纯API模式
                    logger.debug(
                        f"  ⚠️ {symbol}: 数据库数据不足({len(db_klines)}根)，"
                        f"回退到API模式"
                    )
                    # 回退：从API获取完整的100天数据
                    end_date = datetime.now()
                    start_date = end_date - timedelta(days=100)
                    candles = await self.quote_client.get_history_candles(
                        symbol=symbol,
                        period=openapi.Period.Day,
                        adjust_type=openapi.AdjustType.NoAdjust,
                        start=start_date,
                        end=end_date
                    )
            else:
                # 纯API模式（混合模式未启用）
                end_date = datetime.now()
                start_date = end_date - timedelta(days=100)
                candles = await self.quote_client.get_history_candles(
                    symbol=symbol,
                    period=openapi.Period.Day,
                    adjust_type=openapi.AdjustType.NoAdjust,
                    start=start_date,
                    end=end_date
                )

            if not candles or len(candles) < 30:
                logger.debug(f"  ⚠️ {symbol}: 历史数据不足，无法计算指标")
                return None

            # 提取价格数据
            closes = np.array([float(c.close) for c in candles])
            highs = np.array([float(c.high) for c in candles])
            lows = np.array([float(c.low) for c in candles])
            volumes = np.array([c.volume for c in candles])

            # 计算技术指标
            indicators = self._calculate_all_indicators(closes, highs, lows, volumes)

            # 添加成交量比率
            current_volume = quote.volume if quote.volume else 0
            if indicators['volume_sma'] and indicators['volume_sma'] > 0:
                indicators['volume_ratio'] = float(current_volume) / float(indicators['volume_sma'])
            else:
                indicators['volume_ratio'] = 1.0

            return indicators

        except Exception as e:
            logger.debug(f"  ⚠️ {symbol}: 获取技术指标失败 - {e}")
            return None

    def _calculate_exit_score(
        self,
        indicators: Dict,
        position: Dict,
        current_price: float,
        stops: Dict,
        regime: str = "RANGE"
    ) -> Dict:
        """
        基于技术指标计算退出评分和决策

        评分系统（-100 到 +100）:
        - 负分: 应该继续持有（延迟止盈）
        - 正分: 应该平仓
        - 0分: 使用固定止损止盈

        Args:
            indicators: 技术指标字典
            position: 持仓信息
            current_price: 当前价格
            stops: 数据库中的止损止盈设置
            regime: 市场状态 ('BULL' | 'BEAR' | 'RANGE')

        Returns:
            退出决策字典
        """
        score = 0
        reasons = []

        # 计算持仓收益率
        cost_price = position.get('cost_price', 0)
        if cost_price > 0:
            profit_pct = (current_price - cost_price) / cost_price * 100
        else:
            profit_pct = 0

        # === 持有信号（负分）===

        # 1. 强上涨趋势（-30分）
        if not np.isnan(indicators.get('sma_20', np.nan)) and not np.isnan(indicators.get('sma_50', np.nan)):
            sma_20 = indicators['sma_20']
            sma_50 = indicators['sma_50']

            if current_price > sma_20 > sma_50:
                trend_strength = (current_price - sma_20) / sma_20 * 100
                if trend_strength > 5:  # 价格高于SMA20 5%以上
                    score -= 30
                    reasons.append("强上涨趋势")
                elif trend_strength > 2:
                    score -= 20
                    reasons.append("温和上涨趋势")

        # 2. MACD金叉或柱状图扩大（-25分）
        macd_histogram = indicators.get('macd_histogram', 0)
        prev_macd_histogram = indicators.get('prev_macd_histogram', 0)

        if prev_macd_histogram < 0 < macd_histogram:
            score -= 25
            reasons.append("MACD金叉")
        elif macd_histogram > prev_macd_histogram > 0:
            score -= 15
            reasons.append("MACD柱状图扩大")

        # 3. RSI强势区间50-70（-20分）
        rsi = indicators.get('rsi', 50)
        if 50 <= rsi <= 70 and profit_pct > 5:
            score -= 20
            reasons.append(f"RSI强势区间({rsi:.1f})")
        elif rsi < 30 and profit_pct < 0:
            # 超卖且亏损，可能反弹
            score -= 15
            reasons.append(f"RSI超卖({rsi:.1f})，可能反弹")

        # 4. 突破布林带上轨（-15分）
        bb_upper = indicators.get('bb_upper', 0)
        if bb_upper > 0 and current_price >= bb_upper and profit_pct > 5:
            score -= 15
            reasons.append("突破布林带上轨")

        # 5. 成交量持续放大（-10分）
        volume_ratio = indicators.get('volume_ratio', 1.0)
        if volume_ratio >= 1.5 and profit_pct > 5:
            score -= 10
            reasons.append(f"成交量放大({volume_ratio:.1f}x)")

        # === 平仓信号（正分）===

        # 1. MACD趋势止损（增强版）
        macd_line = indicators.get('macd_line', 0)
        macd_signal = indicators.get('macd_signal', 0)

        # 🔥 MACD死叉（+50-70分）- 根据盈亏状态调整
        if prev_macd_histogram > 0 > macd_histogram:
            # 盈利时激进：立即触发
            if profit_pct >= self.settings.profit_aggressive_threshold:
                score += 70
                reasons.append("⚠️ MACD死叉（盈利时激进）")
            # 亏损时稳健：需要额外验证
            elif profit_pct < 0 and self.settings.loss_conservative_mode:
                # 需要配合RSI或0轴跌破才触发
                if rsi > 60 or macd_line < 0:
                    score += 50
                    reasons.append("⚠️ MACD死叉（保守确认）")
                else:
                    score += 20  # 单一死叉信号权重降低
                    reasons.append("MACD死叉（待确认）")
            else:
                score += 50
                reasons.append("⚠️ MACD死叉")

        # 🔥 MACD跌破0轴（+30分）- 趋势彻底反转
        prev_macd_line = indicators.get('prev_macd_line', 0)
        if self.settings.macd_zero_cross_threshold:
            if prev_macd_line > 0 > macd_line:
                score += 30
                reasons.append("⚠️ MACD跌破0轴")
            elif macd_line < 0 and macd_histogram < prev_macd_histogram:
                # 0轴下方且直方图继续萎缩（加速下跌）
                score += 15
                reasons.append("MACD空头加速")

        # 🔥 MACD+RSI组合验证（+20分额外加分）
        if self.settings.macd_rsi_combo:
            if macd_histogram < 0 and rsi > 60:
                # MACD弱势 + RSI超买 = 强卖出信号
                score += 20
                reasons.append("⚠️ MACD弱势+RSI超买")

        # 2. RSI极度超买（+40分）
        if rsi > 80 and profit_pct > 0:
            score += 40
            reasons.append(f"⚠️ RSI极度超买({rsi:.1f})")
        elif rsi > 70 and profit_pct > 5:
            score += 30
            reasons.append(f"RSI超买({rsi:.1f})")

        # 3. 价格远离上轨且RSI回落（+30分）
        bb_middle = indicators.get('bb_middle', 0)
        if bb_upper > 0 and bb_middle > 0:
            bb_range = bb_upper - indicators.get('bb_lower', 0)
            if bb_range > 0:
                bb_position = (current_price - indicators['bb_lower']) / bb_range * 100
                if bb_position < 70 and rsi < 60 and profit_pct > 8:
                    score += 30
                    reasons.append("价格回落且RSI转弱")

        # 4. 均线死叉（+25分）
        if not np.isnan(indicators.get('sma_20', np.nan)) and not np.isnan(indicators.get('sma_50', np.nan)):
            sma_20 = indicators['sma_20']
            sma_50 = indicators['sma_50']

            if sma_20 < sma_50 and current_price < sma_20:
                score += 25
                reasons.append("⚠️ 均线死叉")
            elif current_price < sma_20 and profit_pct < 0:
                score += 20
                reasons.append("跌破SMA20且亏损")

        # 5. 成交量萎缩（+15分）
        if volume_ratio < 0.5 and profit_pct > 8:
            score += 15
            reasons.append("成交量萎缩")

        # 🔥 6. 市场状态调整（Regime Integration）
        if getattr(self.settings, 'regime_exit_score_adjustment', True):
            if regime == "BULL":
                # 牛市：降低卖出倾向，给予持仓更多空间
                score -= 10
                reasons.append("🐂 牛市状态(-10分)")
            elif regime == "BEAR":
                # 熊市：提高卖出倾向，及早离场
                score += 15
                reasons.append("🐻 熊市状态(+15分)")
            # RANGE: 不调整评分

        # 根据评分决定动作（🔥 提高门槛避免过早止盈 + 分批止损 + 渐进式减仓）
        gradual_exit_enabled = getattr(self.settings, 'gradual_exit_enabled', False)
        gradual_exit_threshold_25 = int(getattr(self.settings, 'gradual_exit_threshold_25', 40))
        gradual_exit_threshold_50 = int(getattr(self.settings, 'gradual_exit_threshold_50', 50))

        if score >= 70:  # 从50提高到70
            action = "TAKE_PROFIT_NOW"
            adjusted_take_profit = current_price  # 立即止盈
        elif score >= gradual_exit_threshold_50 and gradual_exit_enabled:
            # 🔥 渐进式减仓50%：评分50-69分时减50%仓位，观察趋势
            action = "PARTIAL_EXIT"
            adjusted_take_profit = current_price * 1.05
        elif score >= gradual_exit_threshold_25 and gradual_exit_enabled:
            # 🔥 渐进式减仓25%：评分40-49分时减25%仓位，观察趋势
            action = "GRADUAL_EXIT"
            adjusted_take_profit = current_price * 1.08
        elif score >= 50 and self.settings.partial_exit_enabled:
            # 传统分批止损：先减50%仓位（向后兼容）
            action = "PARTIAL_EXIT"
            adjusted_take_profit = current_price * 1.05
        elif score >= 50:  # 未启用分批止损时保持原逻辑
            action = "TAKE_PROFIT_EARLY"
            adjusted_take_profit = current_price * 1.05  # 提前止盈（+5%）
        elif score >= 10:
            action = "STANDARD"
            adjusted_take_profit = stops.get('take_profit', current_price * 1.10)
        elif score <= -40:
            action = "STRONG_HOLD"
            adjusted_take_profit = current_price * 1.20  # 延迟到20%
        elif score <= -20:
            action = "DELAY_TAKE_PROFIT"
            adjusted_take_profit = current_price * 1.15  # 延迟到15%
        else:
            action = "STANDARD"
            adjusted_take_profit = stops.get('take_profit', current_price * 1.10)

        # 🔥 ATR动态止损（根据趋势和盈亏状态自适应调整）
        atr = indicators.get('atr', 0)
        if atr and atr > 0 and self.settings.atr_dynamic_enabled:
            # 1. 判断趋势（上涨/下跌/震荡）
            sma_20 = indicators.get('sma_20', 0)
            sma_50 = indicators.get('sma_50', 0)

            # 判断趋势方向
            if not np.isnan(sma_20) and not np.isnan(sma_50) and not np.isnan(macd_line):
                if macd_line > 0 and sma_20 > sma_50:
                    # 上涨趋势：放宽止损
                    trend_multiplier = self.settings.atr_multiplier_bull  # 默认2.5
                    trend_type = "上涨"
                elif macd_line < 0 and sma_20 < sma_50:
                    # 下跌趋势：收紧止损
                    trend_multiplier = self.settings.atr_multiplier_bear  # 默认1.5
                    trend_type = "下跌"
                else:
                    # 震荡趋势：标准止损
                    trend_multiplier = self.settings.atr_multiplier_range  # 默认2.0
                    trend_type = "震荡"
            else:
                # 数据不足，使用标准倍数
                trend_multiplier = 2.0
                trend_type = "标准"

            # 2. 根据盈亏状态调整（混合策略）
            if profit_pct >= self.settings.profit_aggressive_threshold:
                # 盈利>5%时收紧止损，锁定利润
                trend_multiplier *= 0.8
                trend_type += "（盈利收紧）"
            elif profit_pct < -3.0 and self.settings.loss_conservative_mode:
                # 亏损>3%时放宽止损，给予恢复空间
                trend_multiplier *= 1.2
                trend_type += "（亏损放宽）"

            # 3. 计算ATR止损位
            adjusted_stop_loss = current_price - (trend_multiplier * atr)

            # 记录趋势和倍数信息
            reasons.append(f"ATR动态({trend_type}, {trend_multiplier:.1f}x)")

        elif atr and atr > 0:
            # ATR存在但动态调整未启用，使用传统逻辑
            if action in ["STRONG_HOLD", "DELAY_TAKE_PROFIT"]:
                adjusted_stop_loss = current_price - (3.0 * atr)
            else:
                adjusted_stop_loss = current_price - (2.5 * atr)
        else:
            # 无ATR数据，使用固定百分比止损
            if action in ["STRONG_HOLD", "DELAY_TAKE_PROFIT"]:
                adjusted_stop_loss = current_price * 0.93  # -7%
            else:
                adjusted_stop_loss = current_price * 0.95  # -5%

        # 确保不低于原始止损位（保底）
        original_stop = stops.get('stop_loss', 0)
        if original_stop > 0:
            adjusted_stop_loss = max(adjusted_stop_loss, original_stop)

        return {
            'score': score,
            'action': action,
            'reasons': reasons,
            'adjusted_stop_loss': adjusted_stop_loss,
            'adjusted_take_profit': adjusted_take_profit,
            'profit_pct': profit_pct,
        }

    async def check_exit_signals(self, quotes, account, regime: str = "RANGE"):
        """
        检查现有持仓的止损止盈条件（智能版 - 基于技术指标）

        增强功能:
        1. 获取技术指标（RSI, MACD, 布林带, SMA等）
        2. 计算智能退出评分
        3. 根据指标决定是否延迟止盈或提前止损
        4. 保留固定止损止盈作为保底逻辑
        5. 🔥 集成市场状态（牛熊市）调整评分

        Args:
            quotes: 实时行情列表
            account: 账户信息
            regime: 市场状态 ('BULL' | 'BEAR' | 'RANGE')
        """
        exit_signals = []

        try:
            # 获取持仓
            positions = account.get("positions", [])
            if not positions:
                return exit_signals

            # 创建行情字典
            quote_dict = {q.symbol: q for q in quotes}

            for position in positions:
                symbol = position["symbol"]
                quantity = position["quantity"]
                cost_price = position["cost_price"]

                if symbol not in quote_dict:
                    continue

                quote = quote_dict[symbol]
                current_price = float(quote.last_done)

                # 🔥 检查是否在分批止损观察期内
                is_in_observation = False
                partial_exit_data = None
                if self.settings.partial_exit_enabled:
                    try:
                        import json
                        partial_exit_key = f"partial_exit:{account.get('account_id', '')}:{symbol}"
                        partial_exit_str = await self.position_manager._redis.get(partial_exit_key)
                        if partial_exit_str:
                            partial_exit_data = json.loads(partial_exit_str)
                            is_in_observation = True
                            logger.info(
                                f"  👀 {symbol}: 观察期内（部分平仓后）\n"
                                f"     已卖出: {partial_exit_data['partial_qty']}股\n"
                                f"     剩余: {partial_exit_data['remaining_qty']}股\n"
                                f"     观察开始: {partial_exit_data['timestamp']}"
                            )
                    except Exception as e:
                        logger.debug(f"检查观察期状态失败: {e}")

                # 检查是否有止损止盈设置
                stops = await self.stop_manager.get_position_stops(account.get("account_id", ""), symbol)

                if not stops:
                    continue

                # === 智能退出决策 ===
                # 获取技术指标
                indicators = await self._fetch_current_indicators(symbol, quote)

                if indicators:
                    # 计算智能退出评分
                    exit_decision = self._calculate_exit_score(
                        indicators=indicators,
                        position=position,
                        current_price=current_price,
                        stops=stops,
                        regime=regime
                    )

                    action = exit_decision['action']
                    score = exit_decision['score']
                    reasons = exit_decision['reasons']
                    profit_pct = exit_decision['profit_pct']

                    # 记录决策分析
                    logger.debug(
                        f"  📊 {symbol}: 智能分析\n"
                        f"     当前价=${current_price:.2f}, 成本=${cost_price:.2f}, 收益={profit_pct:+.2f}%\n"
                        f"     评分={score:+d}, 动作={action}\n"
                        f"     原因: {', '.join(reasons) if reasons else '无'}"
                    )

                    # 🔥 观察期后的趋势确认逻辑
                    if is_in_observation and partial_exit_data:
                        prev_score = partial_exit_data.get('exit_score', 50)

                        if score >= 60:
                            # 趋势继续恶化，清仓剩余50%
                            logger.error(
                                f"🔴 {symbol}: 观察期确认下跌 - 清仓剩余仓位\n"
                                f"   评分: {prev_score} → {score} (继续恶化)\n"
                                f"   当前=${current_price:.2f}, 收益={profit_pct:+.2f}%\n"
                                f"   原因: {', '.join(reasons)}"
                            )
                            exit_signals.append({
                                'symbol': symbol,
                                'type': 'FULL_EXIT_CONFIRMED',
                                'side': 'SELL',
                                'quantity': quantity,  # 卖出剩余全部
                                'price': current_price,
                                'reason': f"观察期确认下跌，清仓: {', '.join(reasons[:3])}",
                                'score': 95,
                                'timestamp': datetime.now(self.beijing_tz).isoformat(),
                                'priority': 95,
                                'cost_price': cost_price,
                                'entry_time': position.get('entry_time'),
                                'indicators': indicators,
                                'exit_score_details': reasons,
                            })
                            # 清除观察期状态
                            try:
                                partial_exit_key = f"partial_exit:{account.get('account_id', '')}:{symbol}"
                                await self.position_manager._redis.delete(partial_exit_key)
                            except:
                                pass
                            continue  # 已生成清仓信号，跳过后续逻辑

                        elif score < 30:
                            # 趋势恢复，保留剩余仓位
                            logger.success(
                                f"✅ {symbol}: 观察期确认恢复 - 保留剩余仓位\n"
                                f"   评分: {prev_score} → {score} (趋势恢复)\n"
                                f"   当前=${current_price:.2f}, 收益={profit_pct:+.2f}%\n"
                                f"   动作: 继续持有{quantity}股"
                            )
                            # 清除观察期状态
                            try:
                                partial_exit_key = f"partial_exit:{account.get('account_id', '')}:{symbol}"
                                await self.position_manager._redis.delete(partial_exit_key)
                            except:
                                pass
                            continue  # 保留仓位，跳过后续逻辑
                        else:
                            # 趋势不明确，继续观察
                            logger.info(
                                f"  ⏳ {symbol}: 观察期继续 - 趋势不明确\n"
                                f"   评分: {prev_score} → {score}\n"
                                f"   继续观察剩余{quantity}股"
                            )
                            continue  # 继续观察，跳过后续逻辑

                    # 🔥 检查最小持仓时间（智能止盈也需要遵守）
                    entry_time_str = position.get('entry_time')
                    if (
                        self.settings.enable_min_holding_period
                        and entry_time_str
                        and action in ["TAKE_PROFIT_NOW", "TAKE_PROFIT_EARLY"]
                    ):
                        try:
                            entry_time = datetime.fromisoformat(entry_time_str)
                            holding_seconds = (datetime.now(self.beijing_tz) - entry_time).total_seconds()

                            if holding_seconds < self.settings.min_holding_period:
                                holding_minutes = holding_seconds / 60
                                required_minutes = self.settings.min_holding_period / 60
                                logger.info(
                                    f"  ⏭️ {symbol}: 跳过智能止盈 - 持仓时间不足\n"
                                    f"     持仓时长: {holding_minutes:.1f}分钟 < {required_minutes:.0f}分钟\n"
                                    f"     评分={score:+d}, 收益={profit_pct:+.2f}%\n"
                                    f"     原因: {', '.join(reasons[:2])}"
                                )
                                continue  # 跳过这个标的，检查下一个
                        except Exception as e:
                            logger.warning(f"  ⚠️ {symbol}: 解析entry_time失败: {e}")

                    # 🔥 检查最小盈利要求（避免小幅波动就卖出）
                    if action in ["TAKE_PROFIT_NOW", "TAKE_PROFIT_EARLY"]:
                        min_profit_pct = 3.0  # 最小3%盈利
                        if profit_pct < min_profit_pct:
                            logger.debug(
                                f"  ⏭️ {symbol}: 跳过智能止盈 - 盈利不足\n"
                                f"     当前盈利: {profit_pct:.2f}% < {min_profit_pct:.1f}%"
                            )
                            continue  # 跳过这个标的

                    # 根据动作决定是否生成信号
                    if action == "TAKE_PROFIT_NOW":
                        # 立即止盈（忽略固定止盈位）
                        logger.success(
                            f"🎯 {symbol}: 智能止盈 (评分={score:+d})\n"
                            f"   当前=${current_price:.2f}, 收益={profit_pct:+.2f}%\n"
                            f"   原因: {', '.join(reasons)}"
                        )
                        exit_signals.append({
                            'symbol': symbol,
                            'type': 'SMART_TAKE_PROFIT',
                            'side': 'SELL',
                            'quantity': quantity,
                            'price': current_price,
                            'reason': f"智能止盈: {', '.join(reasons[:3])}",  # 前3个原因
                            'score': 95,
                            'timestamp': datetime.now(self.beijing_tz).isoformat(),
                            'priority': 95,
                            # 🔥 增强数据：供Slack通知使用
                            'cost_price': cost_price,
                            'entry_time': position.get('entry_time'),
                            'indicators': indicators,  # 完整的技术指标
                            'exit_score_details': reasons,  # 卖出评分详情
                        })

                    elif action == "PARTIAL_EXIT":
                        # 🔥 分批止损：先卖出50%仓位
                        partial_qty = int(float(quantity) * self.settings.partial_exit_pct)
                        if partial_qty > 0:
                            logger.warning(
                                f"⚠️  {symbol}: 分批止损 - 先减{int(self.settings.partial_exit_pct*100)}%仓位 (评分={score:+d})\n"
                                f"   当前=${current_price:.2f}, 收益={profit_pct:+.2f}%\n"
                                f"   卖出数量: {partial_qty}/{quantity}股\n"
                                f"   原因: {', '.join(reasons)}\n"
                                f"   观察期: {self.settings.partial_exit_observation_minutes}分钟"
                            )
                            exit_signals.append({
                                'symbol': symbol,
                                'type': 'PARTIAL_EXIT',
                                'side': 'SELL',
                                'quantity': partial_qty,  # 🔥 只卖出部分仓位
                                'price': current_price,
                                'reason': f"分批止损({int(self.settings.partial_exit_pct*100)}%): {', '.join(reasons[:3])}",
                                'score': 90,
                                'timestamp': datetime.now(self.beijing_tz).isoformat(),
                                'priority': 90,
                                # 🔥 增强数据：供Slack通知使用
                                'cost_price': cost_price,
                                'entry_time': position.get('entry_time'),
                                'indicators': indicators,  # 完整的技术指标
                                'exit_score_details': reasons,  # 卖出评分详情
                                'is_partial': True,  # 标记为部分平仓
                                'remaining_qty': int(float(quantity)) - partial_qty,
                            })

                            # 🔥 记录部分平仓状态到Redis（用于观察期判断）
                            try:
                                import json
                                partial_exit_key = f"partial_exit:{account.get('account_id', '')}:{symbol}"
                                partial_exit_data = {
                                    'timestamp': datetime.now(self.beijing_tz).isoformat(),
                                    'partial_qty': partial_qty,
                                    'remaining_qty': int(float(quantity)) - partial_qty,
                                    'exit_score': score,
                                    'price': float(current_price),
                                }
                                await self.position_manager._redis.setex(
                                    partial_exit_key,
                                    self.settings.partial_exit_observation_minutes * 60,  # TTL = 观察期
                                    json.dumps(partial_exit_data)
                                )
                            except Exception as e:
                                logger.warning(f"记录部分平仓状态失败: {e}")

                    elif action == "GRADUAL_EXIT":
                        # 🔥 渐进式减仓：卖出25%仓位
                        gradual_qty = int(quantity * 0.25)
                        if gradual_qty > 0:
                            logger.warning(
                                f"📉 {symbol}: 渐进式减仓 - 先减25%仓位 (评分={score:+d})\n"
                                f"   当前=${current_price:.2f}, 收益={profit_pct:+.2f}%\n"
                                f"   卖出数量: {gradual_qty}/{quantity}股\n"
                                f"   原因: {', '.join(reasons)}\n"
                                f"   观察期: {self.settings.partial_exit_observation_minutes}分钟"
                            )
                            exit_signals.append({
                                'symbol': symbol,
                                'type': 'GRADUAL_EXIT',
                                'side': 'SELL',
                                'quantity': gradual_qty,  # 🔥 只卖出25%仓位
                                'price': current_price,
                                'reason': f"渐进式减仓(25%): {', '.join(reasons[:3])}",
                                'score': 85,
                                'timestamp': datetime.now(self.beijing_tz).isoformat(),
                                'priority': 85,
                                # 🔥 增强数据：供Slack通知使用
                                'cost_price': cost_price,
                                'entry_time': position.get('entry_time'),
                                'indicators': indicators,  # 完整的技术指标
                                'exit_score_details': reasons,  # 卖出评分详情
                                'is_partial': True,  # 标记为部分平仓
                                'remaining_qty': quantity - gradual_qty,
                            })

                            # 🔥 记录部分平仓状态到Redis（用于观察期判断）
                            try:
                                import json
                                partial_exit_key = f"partial_exit:{account.get('account_id', '')}:{symbol}"
                                partial_exit_data = {
                                    'timestamp': datetime.now(self.beijing_tz).isoformat(),
                                    'partial_qty': gradual_qty,
                                    'remaining_qty': quantity - gradual_qty,
                                    'exit_score': score,
                                    'price': current_price,
                                }
                                await self.position_manager._redis.setex(
                                    partial_exit_key,
                                    self.settings.partial_exit_observation_minutes * 60,  # TTL = 观察期
                                    json.dumps(partial_exit_data)
                                )
                            except Exception as e:
                                logger.warning(f"记录渐进式减仓状态失败: {e}")

                    elif action == "TAKE_PROFIT_EARLY":
                        # 提前止盈（不等固定止盈位）
                        logger.info(
                            f"🎯 {symbol}: 提前止盈信号 (评分={score:+d})\n"
                            f"   当前=${current_price:.2f}, 收益={profit_pct:+.2f}%\n"
                            f"   原因: {', '.join(reasons)}"
                        )
                        exit_signals.append({
                            'symbol': symbol,
                            'type': 'EARLY_TAKE_PROFIT',
                            'side': 'SELL',
                            'quantity': quantity,
                            'price': current_price,
                            'reason': f"提前止盈: {', '.join(reasons[:3])}",
                            'score': 85,
                            'timestamp': datetime.now(self.beijing_tz).isoformat(),
                            'priority': 85,
                            # 🔥 增强数据：供Slack通知使用
                            'cost_price': cost_price,
                            'entry_time': position.get('entry_time'),
                            'indicators': indicators,  # 完整的技术指标
                            'exit_score_details': reasons,  # 卖出评分详情
                        })

                    elif action in ["STRONG_HOLD", "DELAY_TAKE_PROFIT"]:
                        # 延迟止盈（即使达到固定止盈位也不卖）
                        if current_price >= stops.get('take_profit', float('inf')):
                            logger.info(
                                f"⏸️  {symbol}: 延迟止盈 (评分={score:+d})\n"
                                f"   已达固定止盈(${stops['take_profit']:.2f})，但指标显示持有\n"
                                f"   当前=${current_price:.2f}, 收益={profit_pct:+.2f}%\n"
                                f"   原因: {', '.join(reasons)}\n"
                                f"   新止盈目标: ${exit_decision['adjusted_take_profit']:.2f}"
                            )
                            # 不生成信号，继续持有

                    elif action == "STANDARD":
                        # 使用固定止损止盈逻辑
                        pass  # 继续执行下面的固定逻辑

                # === 固定止损止盈逻辑（保底 + 未获取指标时使用）===
                # 即使有智能决策，固定止损仍然作为保底

                # 检查固定止损
                if stops.get('stop_loss') and current_price <= stops['stop_loss']:
                    logger.warning(
                        f"🛑 {symbol}: 触发固定止损 "
                        f"(当前=${current_price:.2f}, 止损=${stops['stop_loss']:.2f})"
                    )
                    exit_signals.append({
                        'symbol': symbol,
                        'type': 'STOP_LOSS',
                        'side': 'SELL',
                        'quantity': quantity,
                        'price': current_price,
                        'reason': f"触发固定止损 (${stops['stop_loss']:.2f})",
                        'score': 100,
                        'timestamp': datetime.now(self.beijing_tz).isoformat(),
                        'priority': 100,
                        # 🔥 增强数据：供Slack通知使用
                        'cost_price': cost_price,
                        'entry_time': position.get('entry_time'),
                        'indicators': indicators if indicators else {},
                    })

                # 检查固定止盈（仅在没有智能决策或决策为STANDARD时）
                elif stops.get('take_profit') and current_price >= stops['take_profit']:
                    # 如果有指标分析且建议持有，则不执行固定止盈
                    if indicators:
                        exit_decision = self._calculate_exit_score(
                            indicators, position, current_price, stops, regime
                        )
                        if exit_decision['action'] in ["STRONG_HOLD", "DELAY_TAKE_PROFIT"]:
                            # 已经在上面记录日志了，这里跳过
                            continue

                    logger.info(
                        f"🎯 {symbol}: 触发固定止盈 "
                        f"(当前=${current_price:.2f}, 止盈=${stops['take_profit']:.2f})"
                    )
                    exit_signals.append({
                        'symbol': symbol,
                        'type': 'TAKE_PROFIT',
                        'side': 'SELL',
                        'quantity': quantity,
                        'price': current_price,
                        'reason': f"触发固定止盈 (${stops['take_profit']:.2f})",
                        'score': 90,
                        'timestamp': datetime.now(self.beijing_tz).isoformat(),
                        'priority': 90,
                        # 🔥 增强数据：供Slack通知使用
                        'cost_price': cost_price,
                        'entry_time': position.get('entry_time'),
                        'indicators': indicators if indicators else {},
                    })

        except Exception as e:
            logger.error(f"❌ 检查退出信号失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())

        return exit_signals

    async def check_add_position_signals(self, quotes, account, regime: str = "RANGE"):
        """
        检查是否应该对盈利持仓加仓

        策略：当盈利持仓健康且出现新的强买入信号时，适度加仓（10-20%）

        条件：
        1. 持仓健康：exit_score > -30（无明显卖出信号）
        2. 持仓盈利：profit_pct > 2%（已有2%以上盈利）
        3. 市场环境：regime in ['BULL', 'RANGE']（牛市或震荡市）
        4. 新信号强度：buy_signal_score >= 60（出现强买入信号）
        5. 仓位限制：position_pct < MAX_POSITION_PCT（未超过最大仓位）
        6. 冷却期：距离上次加仓 > COOLDOWN（避免频繁操作）

        Args:
            quotes: 实时行情列表
            account: 账户信息
            regime: 市场状态 ('BULL' | 'BEAR' | 'RANGE')

        Returns:
            加仓信号列表
        """
        add_signals = []

        # 检查功能是否启用
        if not getattr(self.settings, 'add_position_enabled', False):
            return add_signals

        # 检查市场环境（熊市不加仓）
        if regime == "BEAR":
            logger.debug("🐻 熊市状态，跳过加仓检查")
            return add_signals

        try:
            # 获取持仓
            positions = account.get("positions", [])
            if not positions:
                return add_signals

            # 创建行情字典
            quote_dict = {q.symbol: q for q in quotes}

            # 获取配置参数
            min_profit_pct = float(getattr(self.settings, 'add_position_min_profit_pct', 2.0))
            min_signal_score = int(getattr(self.settings, 'add_position_min_signal_score', 60))
            max_position_pct = float(getattr(self.settings, 'add_position_max_position_pct', 0.20))
            add_pct = float(getattr(self.settings, 'add_position_pct', 0.15))  # 默认加15%
            cooldown_minutes = int(getattr(self.settings, 'add_position_cooldown_minutes', 60))

            for position in positions:
                symbol = position["symbol"]
                quantity = position["quantity"]
                cost_price = position["cost_price"]

                if symbol not in quote_dict:
                    continue

                quote = quote_dict[symbol]
                current_price = float(quote.last_done)

                # 1. 检查持仓盈利状态
                if cost_price > 0:
                    profit_pct = (current_price - cost_price) / cost_price * 100
                else:
                    profit_pct = 0

                if profit_pct < min_profit_pct:
                    logger.debug(f"  ⏭️ {symbol}: 盈利不足 ({profit_pct:.2f}% < {min_profit_pct}%)")
                    continue

                # 2. 检查持仓健康度（使用exit_score）
                indicators = await self._fetch_current_indicators(symbol, quote)
                if not indicators:
                    logger.debug(f"  ⏭️ {symbol}: 无法获取技术指标")
                    continue

                stops = await self.stop_manager.get_position_stops(account.get("account_id", ""), symbol)
                if not stops:
                    continue

                exit_decision = self._calculate_exit_score(
                    indicators=indicators,
                    position=position,
                    current_price=current_price,
                    stops=stops,
                    regime=regime
                )

                exit_score = exit_decision['score']
                if exit_score > -30:  # 健康度不足（有明显卖出信号）
                    logger.debug(f"  ⏭️ {symbol}: 持仓健康度不足 (exit_score={exit_score:+d} > -30)")
                    continue

                # 3. 检查是否有新的强买入信号
                # 这里需要重新分析当前标的，获取买入评分
                signal = await self.analyze_symbol_and_generate_signal(symbol, quote, current_price)
                if not signal or signal['type'] not in ['BUY', 'STRONG_BUY']:
                    logger.debug(f"  ⏭️ {symbol}: 无强买入信号")
                    continue

                buy_signal_score = signal.get('score', 0)
                if buy_signal_score < min_signal_score:
                    logger.debug(f"  ⏭️ {symbol}: 买入信号不足 (score={buy_signal_score} < {min_signal_score})")
                    continue

                # 4. 检查仓位比例（TODO: 需要获取总资产计算仓位占比）
                # 简化：假设通过quantity判断是否已经太大
                # 这里可以后续优化为基于总资产的仓位百分比

                # 5. 检查冷却期
                try:
                    add_history_key = f"add_position:{account.get('account_id', '')}:{symbol}"
                    last_add_str = await self.position_manager._redis.get(add_history_key)
                    if last_add_str:
                        from dateutil import parser
                        last_add_time = parser.parse(last_add_str)
                        now = datetime.now(self.beijing_tz)
                        elapsed_minutes = (now - last_add_time.astimezone(self.beijing_tz)).total_seconds() / 60
                        if elapsed_minutes < cooldown_minutes:
                            logger.debug(f"  ⏭️ {symbol}: 加仓冷却期内 ({elapsed_minutes:.0f}/{cooldown_minutes}分钟)")
                            continue
                except Exception as e:
                    logger.debug(f"  检查加仓冷却期失败: {e}")

                # 所有条件满足，生成加仓信号
                add_qty = int(quantity * add_pct)
                if add_qty > 0:
                    logger.success(
                        f"📈 {symbol}: 智能加仓信号\n"
                        f"   持仓健康 (exit_score={exit_score:+d}), 盈利={profit_pct:+.2f}%\n"
                        f"   新信号评分={buy_signal_score}, 市场={regime}\n"
                        f"   加仓数量: +{add_qty}股 (+{int(add_pct*100)}%)\n"
                        f"   原因: {signal['reason']}"
                    )
                    add_signals.append({
                        'symbol': symbol,
                        'type': 'ADD_POSITION',
                        'side': 'BUY',
                        'quantity': add_qty,
                        'price': current_price,
                        'reason': f"加仓(+{int(add_pct*100)}%): 持仓健康+强信号",
                        'score': buy_signal_score,
                        'timestamp': datetime.now(self.beijing_tz).isoformat(),
                        'priority': buy_signal_score,
                        # 增强数据
                        'cost_price': cost_price,
                        'current_position_qty': quantity,
                        'profit_pct': profit_pct,
                        'exit_score': exit_score,
                        'regime': regime,
                    })

                    # 记录加仓时间到Redis
                    try:
                        add_history_key = f"add_position:{account.get('account_id', '')}:{symbol}"
                        await self.position_manager._redis.setex(
                            add_history_key,
                            cooldown_minutes * 60,
                            datetime.now(self.beijing_tz).isoformat()
                        )
                    except Exception as e:
                        logger.warning(f"记录加仓时间失败: {e}")

        except Exception as e:
            logger.error(f"❌ 检查加仓信号失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())

        return add_signals

    def _format_signal_technical_analysis(self, signal: Dict) -> List[str]:
        """
        格式化信号的技术分析信息

        Args:
            signal: 信号字典（包含indicators和reasons）

        Returns:
            格式化的文本行列表
        """
        lines = []

        # 买入理由
        reasons = signal.get('reasons', [])
        if reasons:
            lines.append(f"• 买入理由：{', '.join(reasons)}")

        # 技术指标详情
        indicators = signal.get('indicators', {})
        if indicators:
            lines.append(f"• 技术指标：")

            # RSI
            rsi = indicators.get('rsi')
            if rsi is not None:
                rsi_status = "超卖" if rsi < 30 else "偏低" if rsi < 50 else "中性" if rsi < 70 else "超买"
                lines.append(f"  - RSI: {rsi:.1f} ({rsi_status})")

            # MACD
            macd = indicators.get('macd')
            macd_signal = indicators.get('macd_signal')
            if macd is not None and macd_signal is not None:
                macd_status = "金叉" if macd > macd_signal else "死叉"
                lines.append(f"  - MACD: {macd_status} (DIF:{macd:.3f}, DEA:{macd_signal:.3f})")

            # 布林带
            bb_upper = indicators.get('bb_upper')
            bb_middle = indicators.get('bb_middle')
            bb_lower = indicators.get('bb_lower')
            price = signal.get('price', 0)
            if bb_upper and bb_middle and bb_lower and price > 0:
                if price < bb_lower:
                    bb_status = f"下轨支撑 (${bb_lower:.2f})"
                elif price > bb_upper:
                    bb_status = f"上轨压力 (${bb_upper:.2f})"
                else:
                    bb_status = f"中轨附近 (${bb_middle:.2f})"
                lines.append(f"  - 布林带: {bb_status}")

            # 成交量
            volume_ratio = indicators.get('volume_ratio')
            if volume_ratio is not None:
                vol_status = "放量" if volume_ratio > 1.5 else "缩量" if volume_ratio < 0.8 else "正常"
                lines.append(f"  - 成交量比: {volume_ratio:.1f}x ({vol_status})")

            # 均线
            sma_20 = indicators.get('sma_20')
            sma_50 = indicators.get('sma_50')
            if sma_20 and sma_50 and price > 0:
                if price > sma_20 > sma_50:
                    ma_status = "多头排列"
                elif price < sma_20 < sma_50:
                    ma_status = "空头排列"
                else:
                    ma_status = "均线纠缠"
                lines.append(f"  - 均线: {ma_status} (MA20:${sma_20:.2f}, MA50:${sma_50:.2f})")

        return lines

    async def _check_buying_power_before_signal(
        self,
        symbol: str,
        current_price: float,
        signal_score: int,
        signal: Optional[Dict] = None
    ) -> tuple[bool, Optional[str]]:
        """
        在生成买入信号前检查可买数量

        Args:
            symbol: 标的代码
            current_price: 当前价格
            signal_score: 信号评分
            signal: 完整的信号字典（包含indicators和reasons）

        Returns:
            (can_buy, analysis_message): 是否可以买入，以及分析消息（如果不能买入）
        """
        try:
            # 获取手数
            lot_size = await self.lot_size_helper.get_lot_size(symbol, self.quote_client)

            # 调用 API 预估可买数量
            try:
                estimate = await self.trade_client.estimate_max_purchase_quantity(
                    symbol=symbol,
                    order_type=openapi.OrderType.LO,
                    side=openapi.OrderSide.Buy,
                    price=float(current_price)
                )

                # 检查可买数量
                max_qty = estimate.cash_max_qty if hasattr(estimate, 'cash_max_qty') else 0

                if max_qty <= 0:
                    logger.warning(f"  ⚠️ {symbol}: 预估可买数量为0，将分析持仓情况")

                    # 分析持仓并发送通知
                    analysis_msg = await self._analyze_and_notify_positions(
                        symbol=symbol,
                        current_price=current_price,
                        signal_score=signal_score,
                        signal=signal
                    )

                    return False, analysis_msg
                else:
                    logger.debug(f"  ✅ {symbol}: 可买数量 {max_qty} 股")
                    return True, None

            except Exception as e:
                logger.debug(f"  ⚠️ {symbol}: 预估可买数量失败: {e}")
                # API 失败时，继续生成信号（由 order_executor 的 fallback 处理）
                return True, None

        except Exception as e:
            logger.error(f"❌ 买入前检查失败 {symbol}: {e}")
            # 出错时继续生成信号
            return True, None

    def _convert_sell_to_holding_score(self, sell_score: int) -> int:
        """
        将卖出评分转换为持有评分（改进版）

        核心改进：避免简单的 100-x 反向转换，采用非线性映射

        逻辑：
        - 卖出评分0-20（无明显卖出信号）→ 持有评分60-80（中性持仓）
        - 卖出评分20-40（有一些卖出信号）→ 持有评分40-60（中性偏弱）
        - 卖出评分40-60（达到卖出阈值）→ 持有评分20-40（弱势持仓）
        - 卖出评分60+（强烈卖出信号）→ 持有评分0-20（极弱持仓）

        设计原则：
        1. "无卖出信号" ≠ "优质持仓"，最多给到中性偏好(60-80分)
        2. 与买入评分的量级接近，使两者可比（买入评分主要在30-80区间）
        3. 非线性映射，避免过度夸大持仓质量

        Args:
            sell_score: 卖出评分（0-100+）

        Returns:
            持有评分（0-100）
        """
        if sell_score >= 60:
            # 强烈卖出信号：持有评分0-20
            return max(0, 20 - (sell_score - 60) // 2)
        elif sell_score >= 40:
            # 达到卖出阈值：持有评分20-40
            return 40 - (sell_score - 40)
        elif sell_score >= 20:
            # 有一些卖出信号：持有评分40-60
            return 60 - (sell_score - 20)
        else:
            # 无明显卖出信号：持有评分60-80（中性，不是优质）
            return 80 - sell_score

    async def _analyze_position_technical(self, symbol: str, current_price: float) -> Dict:
        """
        对单个持仓进行技术分析，判断是否应该卖出

        Returns:
            {
                'symbol': str,
                'action': 'SELL' | 'HOLD',
                'reason': str,
                'score': int,  # 卖出紧急度评分 0-100
                'signals': []
            }
        """
        try:
            # 获取K线数据
            end_date = datetime.now()
            start_date = end_date - timedelta(days=100)

            candles = await self.quote_client.get_history_candles(
                symbol=symbol,
                period=openapi.Period.Day,
                adjust_type=openapi.AdjustType.NoAdjust,
                start=start_date,
                end=end_date
            )

            if not candles or len(candles) < 30:
                return {'symbol': symbol, 'action': 'HOLD', 'reason': '数据不足', 'score': 0, 'signals': []}

            # 提取数据
            closes = np.array([float(c.close) for c in candles])
            highs = np.array([float(c.high) for c in candles])
            lows = np.array([float(c.low) for c in candles])
            volumes = np.array([c.volume for c in candles])

            # 计算指标
            # 注意：_calculate_all_indicators 返回的是单个值，不是数组
            # 所以我们需要直接使用这些值，而不是取[-1]

            # 计算EMA（需要完整数组）
            ema_short = TechnicalIndicators.ema(closes, 12)
            ema_long = TechnicalIndicators.ema(closes, 26)

            indicators = self._calculate_all_indicators(closes, highs, lows, volumes)

            # 卖出信号分析
            sell_signals = []
            sell_score = 0

            # 1. 趋势反转（下跌趋势）
            if len(ema_short) > 0 and len(ema_long) > 0:
                if ema_short[-1] < ema_long[-1]:
                    sell_signals.append('短期均线跌破长期均线')
                    sell_score += 20

            # 2. MACD死叉
            if not np.isnan(indicators['macd']) and not np.isnan(indicators['macd_signal']):
                if indicators['macd'] < indicators['macd_signal']:
                    sell_signals.append('MACD死叉')
                    sell_score += 15

            # 3. RSI超买
            if not np.isnan(indicators['rsi']):
                if indicators['rsi'] > 70:
                    sell_signals.append(f'RSI超买({indicators["rsi"]:.0f})')
                    sell_score += 10

            # 4. 跌破布林下轨
            if not np.isnan(indicators['bb_lower']):
                if current_price < indicators['bb_lower']:
                    sell_signals.append('跌破布林下轨')
                    sell_score += 15

            # 5. 成交量放大+价格下跌
            if volumes[-1] > np.mean(volumes[-20:]) * 1.5 and closes[-1] < closes[-2]:
                sell_signals.append('放量下跌')
                sell_score += 10

            # 6. 价格跌幅检查
            price_change_5d = (current_price - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 else 0
            price_change_10d = (current_price - closes[-10]) / closes[-10] * 100 if len(closes) >= 10 else 0

            if price_change_5d < -5:
                sell_signals.append(f'5日跌幅{price_change_5d:.1f}%')
                sell_score += 20

            if price_change_10d < -10:
                sell_signals.append(f'10日跌幅{price_change_10d:.1f}%')
                sell_score += 15

            # 判断操作
            if sell_score >= 40:
                action = 'SELL'
                reason = '建议卖出'
            else:
                action = 'HOLD'
                reason = '继续持有'

            return {
                'symbol': symbol,
                'action': action,
                'reason': reason,
                'score': sell_score,
                'signals': sell_signals
            }

        except Exception as e:
            logger.debug(f"分析{symbol}技术指标失败: {e}")
            return {'symbol': symbol, 'action': 'HOLD', 'reason': '分析失败', 'score': 0, 'signals': []}

    async def _analyze_and_notify_positions(
        self,
        symbol: str,
        current_price: float,
        signal_score: int,
        signal: Optional[Dict] = None
    ) -> str:
        """
        分析当前持仓并发送到 Slack（智能显示：有挪仓机会时显示完整分析，无挪仓机会时显示简化通知）

        Args:
            symbol: 触发分析的标的代码
            current_price: 触发分析的标的价格
            signal_score: 信号评分
            signal: 完整的信号字典（包含indicators和reasons）

        Returns:
            分析消息文本
        """
        try:
            # 获取账户信息
            account = await self.trade_client.get_account()

            # 获取持仓
            positions_resp = await self.trade_client.stock_positions()
            positions = []

            if positions_resp and positions_resp.channels:
                for channel in positions_resp.channels:
                    for position in channel.positions:
                        cost_price = float(position.cost_price) if hasattr(position, 'cost_price') else 0
                        quantity = float(position.quantity)
                        market_value = float(position.market_value) if hasattr(position, 'market_value') else 0

                        # 如果market_value为0（非交易时间），使用成本价估算
                        if market_value == 0 and cost_price > 0 and quantity > 0:
                            market_value = cost_price * quantity
                            logger.debug(f"  使用成本价估算{position.symbol}市值: ${market_value:,.0f}")

                        positions.append({
                            'symbol': position.symbol,
                            'quantity': quantity,
                            'available_quantity': float(position.available_quantity) if hasattr(position, 'available_quantity') else quantity,
                            'cost_price': cost_price,
                            'market_value': market_value,
                        })

            # 获取现金和购买力
            cash_info = account.get("cash", {})
            buy_power_info = account.get("buy_power", {})

            # 构建分析消息
            analysis_lines = [
                f"💰 **资金不足 - 无法买入 {symbol}**",
                f"",
                f"📊 **买入信号详情**:",
                f"  • 标的: {symbol}",
                f"  • 价格: ${current_price:.2f}",
                f"  • 评分: {signal_score}/100",
                f"",
                f"💼 **账户状态**:",
            ]

            # 现金和购买力
            for currency in sorted(set(list(cash_info.keys()) + list(buy_power_info.keys()))):
                cash = float(cash_info.get(currency, 0))
                buy_power = float(buy_power_info.get(currency, 0))
                analysis_lines.append(f"  • {currency}: 现金=${cash:,.0f}, 购买力=${buy_power:,.0f}")

            # 持仓分析 - 根据标的币种过滤持仓
            # 确定需要的币种（港股 -> HKD，美股 -> USD）
            target_currency = "HKD" if ".HK" in symbol else "USD"
            target_suffix = ".HK" if target_currency == "HKD" else ".US"

            # 过滤出同币种的持仓
            filtered_positions = [p for p in positions if target_suffix in p['symbol']]

            if filtered_positions:
                # 获取所有持仓的实时价格和技术分析
                symbols = [p['symbol'] for p in filtered_positions]
                try:
                    quotes = await self.quote_client.get_realtime_quote(symbols)
                    quote_dict = {q.symbol: q for q in quotes}
                except Exception as e:
                    logger.warning(f"获取持仓行情失败: {e}")
                    quote_dict = {}

                # 计算总市值
                total_market_value = sum(p['market_value'] for p in filtered_positions)

                # 对每个持仓进行技术分析
                logger.info(f"开始对{len(filtered_positions)}个{target_currency}持仓进行技术分析...")
                positions_with_analysis = []

                for pos in filtered_positions:
                    pos_symbol = pos['symbol']
                    quantity = pos['quantity']
                    cost_price = pos['cost_price']
                    market_value = pos['market_value']

                    # 获取当前价格 - 多级回退策略
                    quote = quote_dict.get(pos_symbol)
                    current = 0.0

                    if quote:
                        if hasattr(quote, 'last_done') and quote.last_done:
                            current = float(quote.last_done)
                        elif hasattr(quote, 'prev_close') and quote.prev_close:
                            current = float(quote.prev_close)
                        elif hasattr(quote, 'open') and quote.open:
                            current = float(quote.open)

                    # 如果还是0，尝试用市值和数量反推
                    if current == 0 and quantity > 0 and market_value > 0:
                        current = market_value / quantity

                    # 如果仍然是0，使用成本价（非交易时间的兜底方案）
                    if current == 0 and cost_price > 0:
                        current = cost_price

                    # 计算盈亏
                    if cost_price > 0 and current > 0:
                        profit_pct = ((current - cost_price) / cost_price) * 100
                        profit_emoji = "🟢" if profit_pct > 0 else "🔴" if profit_pct < 0 else "⚪"
                    else:
                        profit_pct = 0
                        profit_emoji = "⚪"

                    # 技术分析
                    tech_analysis = await self._analyze_position_technical(pos_symbol, current)

                    positions_with_analysis.append({
                        'symbol': pos_symbol,
                        'quantity': quantity,
                        'cost_price': cost_price,
                        'current': current,
                        'market_value': market_value,
                        'profit_pct': profit_pct,
                        'profit_emoji': profit_emoji,
                        'tech': tech_analysis
                    })

                # 按卖出紧急度排序（分数高的排前面）
                positions_sorted = sorted(positions_with_analysis, key=lambda x: x['tech']['score'], reverse=True)

                # 🔥 智能判断：是否有挪仓机会
                # 判断逻辑：
                # 1. 持仓技术面弱势（action='SELL'，卖出评分≥40）
                # 2. 账户使用融资 + 新信号评分较高（≥50）+ 有持仓评分可能较低

                sell_positions = [p for p in positions_sorted if p['tech']['action'] == 'SELL']

                # 🔥 新增：融资账户机会成本分析
                # 如果账户使用了融资（可用资金为负）且有新买入信号，考虑轮换机会
                buy_power = float(buy_power_info.get(target_currency, 0))
                using_margin = buy_power < 0 or float(cash_info.get(target_currency, 0)) < 0

                # 机会成本分析：即使持仓技术面良好，但新信号更优，也提示轮换
                opportunity_cost_positions = []
                if using_margin and signal_score >= 50:  # 新信号至少50分
                    # 寻找评分低于新信号的持仓（考虑机会成本）
                    # 注意：持仓的"卖出评分"高表示更应该卖，我们需要找"持有评分"低的
                    # 使用改进的持有评分计算（非线性映射，避免过度夸大持仓质量）
                    for p in positions_with_analysis:
                        sell_score = p['tech']['score']
                        holding_score = self._convert_sell_to_holding_score(sell_score)

                        # 记录详细评分对比（用于调试）
                        logger.debug(
                            f"    {p['symbol']}: 卖出评分{sell_score} → 持有评分{holding_score} "
                            f"(vs 新信号{signal_score})"
                        )

                        # 如果新信号评分高于持有评分20分以上，考虑轮换
                        if signal_score > holding_score + 20:
                            opportunity_cost_positions.append(p)

                # 合并两类可卖出持仓（按 symbol 去重）
                seen_symbols = set()
                potential_sell_positions = []
                for pos in (sell_positions + opportunity_cost_positions):
                    symbol = pos['symbol']
                    if symbol not in seen_symbols:
                        seen_symbols.add(symbol)
                        potential_sell_positions.append(pos)

                # 如果没有挪仓机会，生成简化通知
                if not potential_sell_positions:
                    logger.info(f"  💡 {target_currency}持仓技术面良好，无挪仓机会，发送简化通知")

                    # 简化通知：只显示信号分析 + 简化账户状态
                    simple_lines = [
                        f"❌ **资金不足 - 无法买入 {symbol}**",
                        f"",
                        f"📊 **买入信号分析**",
                        f"• 标的：{symbol} | 价格：${current_price:.2f} | 评分：{signal_score}/100",
                    ]

                    # 添加信号的技术分析
                    if signal:
                        tech_lines = self._format_signal_technical_analysis(signal)
                        if tech_lines:
                            simple_lines.extend(tech_lines)

                    # 简化的账户状态（只显示相关币种）
                    simple_lines.extend([
                        f"",
                        f"💼 **账户状态**",
                    ])
                    buy_power = float(buy_power_info.get(target_currency, 0))
                    if buy_power < 0:
                        simple_lines.append(f"• {target_currency}购买力：${buy_power:,.0f}（不足）")
                    else:
                        simple_lines.append(f"• {target_currency}购买力：${buy_power:,.0f}")

                    # 建议
                    simple_lines.extend([
                        f"",
                        f"💡 **建议**：当前{len(filtered_positions)}个{target_currency}持仓技术面良好，暂无挪仓机会，等待资金补充"
                    ])

                    analysis_msg = "\n".join(simple_lines)

                    # 发送简化通知（添加限流检查）
                    if hasattr(self, 'slack') and self.slack:
                        notification_key = f"buying_power_insufficient:{symbol}"
                        should_send, skip_reason = self._should_send_slack_notification(notification_key)

                        if should_send:
                            try:
                                await self.slack.send(analysis_msg)
                                logger.info(f"  ✅ 简化通知已发送到 Slack")
                            except Exception as e:
                                logger.warning(f"  ⚠️ 发送 Slack 通知失败: {e}")
                        else:
                            logger.debug(f"  ⏭️ 跳过Slack通知: {skip_reason}")

                    return analysis_msg

                # 有挪仓机会，显示完整分析
                weak_count = len(sell_positions)
                opportunity_count = len(opportunity_cost_positions)
                logger.info(
                    f"  💡 发现{len(potential_sell_positions)}个可挪仓持仓 "
                    f"(技术面弱势{weak_count}个, 机会成本{opportunity_count}个)"
                )

                # 显示持仓分析
                analysis_lines.extend([
                    f"",
                    f"📦 **{target_currency}持仓分析** ({len(filtered_positions)}个，按卖出紧急度排序):",
                ])

                # 🔥 优先显示可挪仓持仓
                for i, pos in enumerate(positions_sorted[:10], 1):
                    position_pct = (pos['market_value'] / total_market_value * 100) if total_market_value > 0 else 0
                    sell_score = pos['tech']['score']
                    holding_score = self._convert_sell_to_holding_score(sell_score)

                    # 判断是否是建议卖出的持仓
                    is_weak = pos in sell_positions
                    is_opportunity = pos in opportunity_cost_positions

                    # 操作建议emoji和文本
                    if is_weak:
                        action_emoji = "🔴"
                        action_text = f"技术面弱势，建议卖出（卖出评分{sell_score}）"
                    elif is_opportunity:
                        action_emoji = "🟡"
                        action_text = f"机会成本：新信号({signal_score}) vs 持仓({holding_score:.0f})"
                    else:
                        action_emoji = "🟢"
                        action_text = f"继续持有（持仓评分{holding_score:.0f}）"

                    # 基本信息
                    line = (
                        f"  {i}. {action_emoji} **{pos['symbol']}** ({action_text}):\n"
                        f"     持仓: {pos['quantity']:.0f}股 @ ${pos['cost_price']:.2f} → ${pos['current']:.2f} "
                        f"({pos['profit_pct']:+.1f}%) | 市值=${pos['market_value']:,.0f} ({position_pct:.1f}%)"
                    )

                    # 添加技术信号
                    if pos['tech']['signals']:
                        signals_text = ", ".join(pos['tech']['signals'][:3])  # 只显示前3个信号
                        line += f"\n     信号: {signals_text} (紧急度{pos['tech']['score']}分)"

                    analysis_lines.append(line)

                if len(filtered_positions) > 10:
                    analysis_lines.append(f"  ... 还有 {len(filtered_positions) - 10} 个{target_currency}持仓")
            else:
                # 无同币种持仓，生成简化通知
                logger.info(f"  💡 无{target_currency}持仓，发送简化通知")

                simple_lines = [
                    f"❌ **资金不足 - 无法买入 {symbol}**",
                    f"",
                    f"📊 **买入信号分析**",
                    f"• 标的：{symbol} | 价格：${current_price:.2f} | 评分：{signal_score}/100",
                ]

                # 添加信号的技术分析
                if signal:
                    tech_lines = self._format_signal_technical_analysis(signal)
                    if tech_lines:
                        simple_lines.extend(tech_lines)

                # 简化的账户状态（只显示相关币种）
                simple_lines.extend([
                    f"",
                    f"💼 **账户状态**",
                ])
                buy_power = float(buy_power_info.get(target_currency, 0))
                if buy_power < 0:
                    simple_lines.append(f"• {target_currency}购买力：${buy_power:,.0f}（不足）")
                else:
                    simple_lines.append(f"• {target_currency}购买力：${buy_power:,.0f}")

                # 建议
                simple_lines.extend([
                    f"",
                    f"💡 **建议**：当前无{target_currency}持仓可挪仓，等待资金补充或考虑跨币种资金调配"
                ])

                analysis_msg = "\n".join(simple_lines)

                # 发送简化通知（添加限流检查）
                if hasattr(self, 'slack') and self.slack:
                    notification_key = f"buying_power_insufficient:{symbol}"
                    should_send, skip_reason = self._should_send_slack_notification(notification_key)

                    if should_send:
                        try:
                            await self.slack.send(analysis_msg)
                            logger.info(f"  ✅ 简化通知已发送到 Slack")
                        except Exception as e:
                            logger.warning(f"  ⚠️ 发送 Slack 通知失败: {e}")
                    else:
                        logger.debug(f"  ⏭️ 跳过Slack通知: {skip_reason}")

                return analysis_msg

            # 建议 - 根据技术分析结果给出具体操作建议
            analysis_lines.append(f"")
            analysis_lines.append(f"💡 **操作建议**:")

            if filtered_positions and 'positions_sorted' in locals():
                # 统计建议卖出的持仓
                sell_positions = [p for p in positions_sorted if p['tech']['action'] == 'SELL']
                hold_positions = [p for p in positions_sorted if p['tech']['action'] == 'HOLD']

                if sell_positions:
                    analysis_lines.append(f"  🔴 **建议卖出** ({len(sell_positions)}个):")
                    for i, pos in enumerate(sell_positions[:5], 1):  # 最多显示5个
                        reason = ", ".join(pos['tech']['signals'][:2]) if pos['tech']['signals'] else pos['tech']['reason']
                        analysis_lines.append(
                            f"     {i}. {pos['symbol']} - {reason} "
                            f"(盈亏{pos['profit_pct']:+.1f}%, 紧急度{pos['tech']['score']}分)"
                        )
                    if len(sell_positions) > 5:
                        analysis_lines.append(f"     ... 还有 {len(sell_positions) - 5} 个建议卖出")
                else:
                    analysis_lines.append(f"  ✅ 当前无明显技术卖出信号")

                if hold_positions and sell_positions:
                    analysis_lines.append(f"")
                    analysis_lines.append(f"  🟢 **可继续持有** ({len(hold_positions)}个)")

                # 资金释放建议
                if sell_positions:
                    total_sellable_value = sum(p['market_value'] for p in sell_positions)
                    analysis_lines.append(f"")
                    analysis_lines.append(
                        f"  💰 卖出上述标的可释放购买力约${total_sellable_value:,.0f}"
                    )

            elif not filtered_positions:
                # 没有同币种持仓
                analysis_lines.append(f"  ⚠️ 当前无{target_currency}持仓可减仓")
                analysis_lines.append(f"  • 等待{target_currency}资金补充")
                analysis_lines.append(f"  • 或考虑跨币种资金调配")
            else:
                # 有持仓但没有技术分析
                analysis_lines.append(f"  • 考虑卖出部分{target_currency}持仓释放购买力")

            analysis_lines.append(f"")
            analysis_lines.append(f"  📌 买入信号: {symbol} (${current_price:.2f}, 评分{signal_score}分)")

            analysis_msg = "\n".join(analysis_lines)

            # 发送到 Slack
            if hasattr(self, 'slack') and self.slack:
                try:
                    await self.slack.send(analysis_msg)
                    logger.info(f"  ✅ 持仓分析已发送到 Slack")
                except Exception as e:
                    logger.warning(f"  ⚠️ 发送 Slack 通知失败: {e}")

            return analysis_msg

        except Exception as e:
            logger.error(f"❌ 分析持仓失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return f"持仓分析失败: {e}"

    async def check_pre_close_rotation(
        self,
        quotes,
        account,
        regime: str = "RANGE"
    ) -> List[Dict]:
        """
        收盘前自动评估并生成轮换卖出信号

        在港股收盘前30分钟（15:30-16:00）或美股收盘前1小时（15:00-16:00 ET）触发
        自动识别弱势持仓并生成卖出信号，为另一个市场释放资金

        Args:
            quotes: 实时行情列表
            account: 账户信息
            regime: 市场状态

        Returns:
            卖出信号列表
        """
        rotation_signals = []

        try:
            # 获取当前时间
            now = datetime.now(self.beijing_tz)

            # 判断是否在收盘前时间窗口
            should_check_hk = False
            should_check_us = False

            # 港股收盘前检查（15:30-16:00）
            if now.hour == 15 and now.minute >= 30:
                should_check_hk = True
                logger.info("🕐 港股收盘前时段：检查港股持仓轮换机会...")
            elif now.hour == 16 and now.minute == 0:
                should_check_hk = True

            # 美股收盘前检查（22:00-23:59 北京时间，对应美东 15:00-16:59）
            # 注意：需要根据夏令时/冬令时调整
            # 简化处理：在22:00-23:59检查
            if now.hour == 22 or now.hour == 23:
                should_check_us = True
                logger.info("🕐 美股收盘前时段：检查美股持仓轮换机会...")

            if not should_check_hk and not should_check_us:
                logger.debug("  ⏭️  非收盘前时段，跳过轮换检查")
                return []

            # 获取持仓
            positions = account.get("positions", [])
            if not positions:
                logger.info("  ℹ️  当前无持仓，跳过轮换检查")
                return []

            # 筛选需要检查的市场持仓
            target_positions = []
            if should_check_hk:
                target_positions = [p for p in positions if p.get("symbol", "").endswith(".HK")]
                logger.info(f"  🇭🇰 港股持仓: {len(target_positions)}个")
            elif should_check_us:
                target_positions = [p for p in positions if p.get("symbol", "").endswith(".US")]
                logger.info(f"  🇺🇸 美股持仓: {len(target_positions)}个")

            if not target_positions:
                logger.info("  ℹ️  目标市场无持仓，跳过轮换检查")
                return []

            # 构建行情字典
            quote_dict = {q.symbol: q for q in quotes}

            # 准备技术指标数据（简化版，使用缓存或快速计算）
            technical_data = {}

            for pos in target_positions:
                symbol = pos.get("symbol")
                try:
                    # 获取当前价格（优先使用实时行情，否则使用持仓价格）
                    quote = quote_dict.get(symbol)
                    current_price = 0

                    if quote and quote.last_done:
                        current_price = float(quote.last_done)
                        logger.debug(f"    {symbol}: 使用实时行情价格 ${current_price:.2f}")
                    elif pos.get("market_price"):
                        # 🔥 Fallback: 使用持仓中的市场价格（收盘后仍可用）
                        current_price = float(pos.get("market_price"))
                        logger.debug(f"    {symbol}: 使用持仓市场价格 ${current_price:.2f} (无实时行情)")
                    else:
                        logger.warning(f"    {symbol}: 无法获取价格，跳过")
                        continue

                    if current_price <= 0:
                        logger.debug(f"    {symbol}: 价格无效，跳过")
                        continue

                    # 获取技术指标（尝试从缓存或快速计算）
                    indicators = await self._fetch_current_indicators(symbol, quote)
                    if indicators:
                        technical_data[symbol] = indicators
                    else:
                        # 如果获取失败，使用空指标
                        technical_data[symbol] = {}

                except Exception as e:
                    logger.debug(f"    {symbol}: 获取数据失败 - {e}")
                    continue

            # 使用时区资金管理器识别可轮换持仓
            logger.info("  🔍 分析持仓轮换评分...")

            rotatable_positions = self.timezone_capital_manager.identify_rotatable_positions(
                positions=target_positions,
                quotes=quote_dict,
                technical_data=technical_data,
                regime=regime,
                target_market="US" if should_check_hk else "HK"  # 为哪个市场释放资金
            )

            # 🔥 港股强制轮换逻辑
            if not rotatable_positions and should_check_hk and self.hk_force_rotation_enabled:
                logger.warning(
                    f"  🔄 港股收盘前强制轮换：虽然无弱势持仓，"
                    f"但仍将卖出最弱的 {self.hk_force_rotation_max} 个港股为美股腾出资金"
                )

                # 获取所有港股持仓的评分
                from dataclasses import dataclass
                from typing import Optional

                @dataclass
                class PositionScore:
                    symbol: str
                    rotation_score: float
                    profit_pct: float
                    market_value: float
                    reason: str
                    quantity: int

                scored_positions = []

                for pos in target_positions:
                    symbol = pos.get("symbol")
                    try:
                        # 🔥 获取当前价格（优先使用实时行情，否则使用持仓价格）
                        quote = quote_dict.get(symbol)
                        current_price = 0

                        if quote and quote.last_done:
                            current_price = float(quote.last_done)
                        elif pos.get("market_price"):
                            # Fallback: 使用持仓中的市场价格（收盘后仍可用）
                            current_price = float(pos.get("market_price"))
                            logger.debug(f"      {symbol}: 使用持仓价格 ${current_price:.2f} (无实时行情)")
                        else:
                            logger.warning(f"      {symbol}: 无法获取价格，跳过评分")
                            continue

                        if current_price <= 0:
                            continue

                        cost_price = float(pos.get('avg_cost', current_price))
                        quantity = float(pos.get('quantity', 0))
                        market_value = current_price * quantity
                        profit_pct = (current_price - cost_price) / cost_price if cost_price > 0 else 0

                        # 计算评分（使用时区资金管理器的评分逻辑）
                        indicators = technical_data.get(symbol, {})

                        # 简化评分：基于盈亏、技术指标
                        score = 50  # 基准分

                        # 盈亏调整
                        if profit_pct < -0.05:
                            score += 30  # 亏损持仓评分高（弱）
                        elif profit_pct < 0:
                            score += 15
                        elif profit_pct > 0.10:
                            score -= 30  # 高盈利持仓评分低（强）
                        elif profit_pct > 0.05:
                            score -= 15

                        # 技术指标调整
                        if indicators.get('trend') == 'down':
                            score += 20
                        elif indicators.get('trend') == 'up':
                            score -= 20

                        if indicators.get('macd_signal') == 'bearish':
                            score += 15
                        elif indicators.get('macd_signal') == 'bullish':
                            score -= 15

                        reason_parts = []
                        if profit_pct < 0:
                            reason_parts.append(f"亏损{profit_pct:.1%}")
                        else:
                            reason_parts.append(f"盈利{profit_pct:.1%}")

                        if indicators.get('trend'):
                            reason_parts.append(f"{indicators['trend']}趋势")

                        reason = ", ".join(reason_parts) if reason_parts else "强制轮换"

                        scored_positions.append(PositionScore(
                            symbol=symbol,
                            rotation_score=score,
                            profit_pct=profit_pct,
                            market_value=market_value,
                            reason=reason,
                            quantity=quantity
                        ))

                    except Exception as e:
                        logger.debug(f"    {symbol}: 评分计算失败 - {e}")
                        continue

                # 按评分排序（评分越高越弱），取最弱的N个
                if scored_positions:
                    scored_positions.sort(key=lambda x: x.rotation_score, reverse=True)
                    rotatable_positions = scored_positions[:self.hk_force_rotation_max]

                    logger.warning(f"  🎯 已选出 {len(rotatable_positions)} 个最弱持仓进行强制轮换")
                    for i, pos in enumerate(rotatable_positions, 1):
                        logger.info(
                            f"    {i}. {pos.symbol}: 评分={pos.rotation_score:.0f}, "
                            f"盈亏={pos.profit_pct:+.1%}, 市值=${pos.market_value:,.0f}"
                        )
                else:
                    logger.warning("  ⚠️  无法为港股持仓评分，跳过强制轮换")
                    return []

            if not rotatable_positions:
                logger.info("  ✅ 无弱势持仓需要轮换")
                return []

            # 生成自动卖出信号
            logger.info(f"  🎯 生成自动卖出信号（{len(rotatable_positions)}个弱势持仓）...")

            for rot_pos in rotatable_positions:
                symbol = rot_pos.symbol

                # 检查是否已有卖出订单（避免重复）
                if symbol in self.sold_today:
                    logger.debug(f"    {symbol}: 今日已有卖出订单，跳过")
                    continue

                # 🔥 从原始持仓获取 quantity（关键修复）
                position = next((p for p in target_positions if p.get('symbol') == symbol), None)
                if not position:
                    logger.warning(f"    {symbol}: 找不到持仓信息，跳过")
                    continue

                quantity = position.get('quantity', 0)
                if quantity <= 0:
                    logger.warning(f"    {symbol}: 持仓数量无效 ({quantity})，跳过")
                    continue

                # 获取当前价格
                quote = quote_dict.get(symbol)
                current_price = float(quote.last_done) if quote and quote.last_done else 0

                if current_price <= 0:
                    logger.debug(f"    {symbol}: 价格无效，跳过")
                    continue

                # 构建卖出信号（添加缺失的 side 和 quantity 字段）
                rotation_signal = {
                    'symbol': symbol,
                    'type': 'ROTATION_SELL',  # 标记为轮换卖出
                    'side': 'SELL',  # 🔥 添加 side 字段
                    'price': current_price,
                    'quantity': quantity,  # 🔥 添加 quantity 字段
                    'reason': f"收盘前自动轮换 (评分={rot_pos.rotation_score:.0f}, 盈亏={rot_pos.profit_pct:+.1%}, 原因={rot_pos.reason})",
                    'score': 90,  # 轮换卖出优先级较高
                    'priority': 90,
                    'timestamp': datetime.now(self.beijing_tz).isoformat(),
                    'metadata': {
                        'rotation_score': rot_pos.rotation_score,
                        'profit_pct': rot_pos.profit_pct,
                        'market_value': rot_pos.market_value,
                        'rotation_reason': rot_pos.reason,
                        'auto_rotation': True,  # 标记为自动轮换
                        'target_market': "US" if should_check_hk else "HK"
                    }
                }

                rotation_signals.append(rotation_signal)

                logger.success(
                    f"    ✅ {symbol}: 生成轮换卖出信号 "
                    f"(数量={quantity}, 评分={rot_pos.rotation_score:.0f}, "
                    f"盈亏={rot_pos.profit_pct:+.1%}, "
                    f"市值=${rot_pos.market_value:,.0f})"
                )

            # 发送通知
            if rotation_signals and hasattr(self, 'slack') and self.slack:
                market_name = "港股" if should_check_hk else "美股"
                target_market_name = "美股" if should_check_hk else "港股"

                notification_lines = [
                    f"🔄 **{market_name}收盘前自动轮换**",
                    f"",
                    f"为{target_market_name}交易时段释放资金，准备卖出以下弱势持仓：",
                    f""
                ]

                for i, signal in enumerate(rotation_signals[:5], 1):
                    metadata = signal.get('metadata', {})
                    profit_pct = metadata.get('profit_pct', 0)
                    market_value = metadata.get('market_value', 0)
                    score = metadata.get('rotation_score', 0)

                    profit_emoji = "🟢" if profit_pct > 0 else "🔴"

                    notification_lines.append(
                        f"{i}. {signal['symbol']} - ${signal['price']:.2f} "
                        f"{profit_emoji} {profit_pct:+.1%} "
                        f"(市值${market_value:,.0f}, 评分{score:.0f})"
                    )

                if len(rotation_signals) > 5:
                    notification_lines.append(f"... 还有 {len(rotation_signals) - 5} 个")

                total_value = sum(
                    s.get('metadata', {}).get('market_value', 0)
                    for s in rotation_signals
                )

                notification_lines.extend([
                    f"",
                    f"💰 预计释放购买力: ${total_value * 0.8:,.0f}",
                    f"🎯 目标市场: {target_market_name}",
                    f"⏰ 触发时间: {now.strftime('%H:%M:%S')}"
                ])

                try:
                    await self.slack.send("\n".join(notification_lines))
                    logger.info("  ✅ 轮换通知已发送")
                except Exception as e:
                    logger.warning(f"  ⚠️ 发送通知失败: {e}")

            return rotation_signals

        except Exception as e:
            logger.error(f"❌ 收盘前轮换检查失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return []

    async def check_realtime_rotation(
        self,
        quotes,
        account,
        regime: str = "RANGE"
    ) -> List[Dict]:
        """
        实时挪仓检查：当有高分信号但资金不足时，立即评估并卖出弱势持仓

        与收盘前轮换的区别：
        - 收盘前轮换：定时触发（15:30-16:00 或 22:00-23:59）
        - 实时轮换：事件触发（出现高分信号但资金不足时）

        Args:
            quotes: 实时行情列表
            account: 账户信息
            regime: 市场状态

        Returns:
            卖出信号列表
        """
        rotation_signals = []

        try:
            # 检查是否启用实时挪仓
            if not getattr(self.settings, 'realtime_rotation_enabled', True):
                logger.debug("  ⏭️  实时挪仓未启用，跳过检查")
                return []

            # 🔥 从Redis检查是否有高分信号因资金不足而延迟或失败
            try:
                # 1. 获取延迟信号列表（重试队列）
                delayed_signals = await self.signal_queue.get_delayed_signals(
                    account=self.settings.account_id
                )

                # 2. 获取失败信号列表（失败队列中5分钟内的高分信号）
                failed_signals = await self.signal_queue.get_failed_signals(
                    account=self.settings.account_id,
                    min_score=getattr(self.settings, 'realtime_rotation_min_signal_score', 60),
                    max_age_seconds=300  # 只考虑5分钟内失败的信号
                )

                # 合并延迟信号和失败信号
                all_pending_signals = delayed_signals + failed_signals

                if not all_pending_signals:
                    logger.debug("  ⏭️  无延迟或失败的高分信号，跳过实时挪仓检查")
                    return []

                # 筛选高分买入信号
                high_score_pending = [
                    s for s in all_pending_signals
                    if s.get('score', 0) >= getattr(self.settings, 'realtime_rotation_min_signal_score', 60)
                    and s.get('side') == 'BUY'
                ]

                if not high_score_pending:
                    logger.debug("  ⏭️  待处理信号评分不够高，跳过实时挪仓检查")
                    return []

                # 记录信号来源
                delayed_count = len([s for s in high_score_pending if 'retry_after' in s])
                failed_count = len([s for s in high_score_pending if 'failed_at' in s])

                logger.info(
                    f"🔔 检测到 {len(high_score_pending)} 个高分信号因资金不足无法买入 "
                    f"(延迟重试: {delayed_count}, 已失败: {failed_count})"
                )

                # 保存待处理信号以便后续恢复
                self._pending_rotation_signals = high_score_pending

            except Exception as e:
                logger.debug(f"  ⚠️  检查待处理信号失败: {e}")
                return []

            # 获取持仓
            positions = account.get("positions", [])
            if not positions:
                logger.info("  ℹ️  当前无持仓，无法实时挪仓")
                return []

            # 构建行情字典
            quote_dict = {q.symbol: q for q in quotes}

            # 准备技术指标数据
            technical_data = {}

            for pos in positions:
                symbol = pos.get("symbol")
                try:
                    quote = quote_dict.get(symbol)
                    if not quote or not quote.last_done:
                        continue

                    # 获取技术指标（尝试从缓存或快速计算）
                    indicators = await self._fetch_current_indicators(symbol, quote)
                    if indicators:
                        technical_data[symbol] = indicators
                    else:
                        technical_data[symbol] = {}

                except Exception as e:
                    logger.debug(f"    {symbol}: 获取数据失败 - {e}")
                    continue

            # 分析每个延迟的高分信号
            for delayed_signal in high_score_pending:
                signal_symbol = delayed_signal.get('symbol')
                signal_score = delayed_signal.get('score', 0)
                signal_market = "HK" if signal_symbol.endswith(".HK") else "US" if signal_symbol.endswith(".US") else "A"

                logger.info(f"\n🎯 分析高分延迟信号: {signal_symbol} (评分={signal_score})")

                # 只检查同市场的持仓（释放同币种资金）
                same_market_positions = [
                    p for p in positions
                    if (signal_market == "HK" and p.get("symbol", "").endswith(".HK"))
                    or (signal_market == "US" and p.get("symbol", "").endswith(".US"))
                    or (signal_market == "A" and p.get("symbol", "").endswith(".SH") or p.get("symbol", "").endswith(".SZ"))
                ]

                if not same_market_positions:
                    logger.info(f"  ℹ️  {signal_market}市场无持仓，无法挪仓")
                    continue

                logger.info(f"  📊 {signal_market}市场持仓: {len(same_market_positions)}个")

                # 评估持仓质量（使用简化评分逻辑）
                from dataclasses import dataclass

                @dataclass
                class PositionScore:
                    symbol: str
                    rotation_score: float
                    profit_pct: float
                    market_value: float
                    reason: str
                    quantity: int

                scored_positions = []

                for pos in same_market_positions:
                    symbol = pos.get("symbol")
                    try:
                        quote = quote_dict.get(symbol)
                        if not quote or not quote.last_done:
                            continue

                        current_price = float(quote.last_done)
                        cost_price = float(pos.get('avg_cost', current_price))
                        quantity = float(pos.get('quantity', 0))
                        market_value = current_price * quantity
                        profit_pct = (current_price - cost_price) / cost_price if cost_price > 0 else 0

                        # 计算评分（使用与check_pre_close_rotation相同的逻辑）
                        indicators = technical_data.get(symbol, {})

                        score = 50  # 基准分

                        # 盈亏调整
                        if profit_pct < -0.10:
                            score += 30  # 大幅亏损
                        elif profit_pct < -0.05:
                            score += 20
                        elif profit_pct < 0:
                            score += 10
                        elif profit_pct > 0.15:
                            score -= 30  # 高盈利
                        elif profit_pct > 0.10:
                            score -= 20
                        elif profit_pct > 0.05:
                            score -= 10

                        # 技术指标调整
                        if indicators.get('trend') == 'down':
                            score += 20
                        elif indicators.get('trend') == 'up':
                            score -= 20

                        if indicators.get('macd_signal') == 'bearish':
                            score += 15
                        elif indicators.get('macd_signal') == 'bullish':
                            score -= 15

                        reason_parts = []
                        if profit_pct < 0:
                            reason_parts.append(f"亏损{profit_pct:.1%}")
                        else:
                            reason_parts.append(f"盈利{profit_pct:.1%}")

                        if indicators.get('trend'):
                            reason_parts.append(f"{indicators['trend']}趋势")

                        reason = ", ".join(reason_parts) if reason_parts else "实时挪仓"

                        scored_positions.append(PositionScore(
                            symbol=symbol,
                            rotation_score=score,
                            profit_pct=profit_pct,
                            market_value=market_value,
                            reason=reason,
                            quantity=quantity
                        ))

                    except Exception as e:
                        logger.debug(f"    {symbol}: 评分计算失败 - {e}")
                        continue

                if not scored_positions:
                    logger.info(f"  ℹ️  无法评估持仓，跳过挪仓")
                    continue

                # 按评分排序（评分越高越弱）
                scored_positions.sort(key=lambda x: x.rotation_score, reverse=True)

                # 🔥 只卖出评分显著低于新信号的持仓
                min_score_diff = getattr(self.settings, 'realtime_rotation_min_score_diff', 10)
                max_rotations = getattr(self.settings, 'realtime_rotation_max_positions', 1)  # 默认每次只卖1个

                weak_positions = [
                    p for p in scored_positions
                    if (signal_score - p.rotation_score) >= min_score_diff
                ]

                if not weak_positions:
                    logger.info(
                        f"  ✅ 无弱势持仓（需新信号评分高出持仓至少{min_score_diff}分）"
                    )
                    logger.info(f"  📊 最弱持仓评分: {scored_positions[0].rotation_score:.0f} vs 新信号: {signal_score}")
                    continue

                # 取最弱的N个持仓
                positions_to_sell = weak_positions[:max_rotations]

                logger.info(
                    f"  🎯 找到 {len(positions_to_sell)} 个弱势持仓可挪仓 "
                    f"(新信号{signal_score}分 vs 持仓{positions_to_sell[0].rotation_score:.0f}分)"
                )

                # 生成卖出信号
                for rot_pos in positions_to_sell:
                    # 检查是否已有卖出订单（避免重复）
                    if rot_pos.symbol in self.sold_today:
                        logger.debug(f"    {rot_pos.symbol}: 今日已有卖出订单，跳过")
                        continue

                    # 获取当前价格
                    quote = quote_dict.get(rot_pos.symbol)
                    current_price = float(quote.last_done) if quote and quote.last_done else 0

                    if current_price <= 0:
                        logger.debug(f"    {rot_pos.symbol}: 价格无效，跳过")
                        continue

                    # 构建卖出信号
                    rotation_signal = {
                        'symbol': rot_pos.symbol,
                        'type': 'REALTIME_ROTATION_SELL',  # 标记为实时轮换卖出
                        'side': 'SELL',
                        'price': current_price,
                        'quantity': rot_pos.quantity,
                        'reason': f"实时挪仓释放资金 (为{signal_symbol}腾出资金, 评分{rot_pos.rotation_score:.0f}<{signal_score}, {rot_pos.reason})",
                        'score': 95,  # 实时轮换优先级更高
                        'priority': 95,
                        'timestamp': datetime.now(self.beijing_tz).isoformat(),
                        'metadata': {
                            'rotation_score': rot_pos.rotation_score,
                            'profit_pct': rot_pos.profit_pct,
                            'market_value': rot_pos.market_value,
                            'rotation_reason': rot_pos.reason,
                            'auto_rotation': True,
                            'realtime_rotation': True,  # 标记为实时轮换
                            'target_signal': signal_symbol,  # 为哪个信号释放资金
                            'target_score': signal_score
                        }
                    }

                    rotation_signals.append(rotation_signal)

                    logger.success(
                        f"    ✅ {rot_pos.symbol}: 生成实时挪仓信号 "
                        f"(评分={rot_pos.rotation_score:.0f}, 盈亏={rot_pos.profit_pct:+.1%}, "
                        f"市值=${rot_pos.market_value:,.0f})"
                    )

            # 发送通知
            if rotation_signals and hasattr(self, 'slack') and self.slack:
                notification_lines = [
                    f"🔄 **实时挪仓触发**",
                    f"",
                    f"检测到高分信号因资金不足延迟，立即释放资金：",
                    f""
                ]

                for i, signal in enumerate(rotation_signals[:3], 1):
                    metadata = signal.get('metadata', {})
                    profit_pct = metadata.get('profit_pct', 0)
                    market_value = metadata.get('market_value', 0)
                    target_signal = metadata.get('target_signal', 'N/A')

                    profit_emoji = "🟢" if profit_pct > 0 else "🔴"

                    notification_lines.append(
                        f"{i}. 卖出 {signal['symbol']} - ${signal['price']:.2f} "
                        f"{profit_emoji} {profit_pct:+.1%} "
                        f"(市值${market_value:,.0f})"
                    )
                    notification_lines.append(f"   → 为 {target_signal} 释放资金")

                if len(rotation_signals) > 3:
                    notification_lines.append(f"   ... 还有 {len(rotation_signals)-3} 个")

                notification_lines.extend([
                    f"",
                    f"⏰ 触发时间: {datetime.now(self.beijing_tz).strftime('%H:%M:%S')}"
                ])

                try:
                    await self.slack.send("\n".join(notification_lines))
                    logger.info("  ✅ 实时挪仓通知已发送")
                except Exception as e:
                    logger.warning(f"  ⚠️ 发送通知失败: {e}")

            # 🔥 如果生成了挪仓信号，尝试恢复失败队列中的高分信号
            if rotation_signals and hasattr(self, '_pending_rotation_signals'):
                recovered_count = 0
                for signal in self._pending_rotation_signals:
                    # 只恢复来自失败队列的信号
                    if 'failed_at' in signal:
                        try:
                            success = await self.signal_queue.recover_failed_signal(signal)
                            if success:
                                recovered_count += 1
                        except Exception as e:
                            logger.warning(f"  ⚠️ 恢复失败信号 {signal.get('symbol')} 失败: {e}")

                if recovered_count > 0:
                    logger.success(f"  ✅ 已恢复 {recovered_count} 个失败信号到队列，等待重新执行")

            return rotation_signals

        except Exception as e:
            logger.error(f"❌ 实时挪仓检查失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return []

    async def check_urgent_sells(
        self,
        quotes,
        account
    ) -> List[Dict]:
        """
        检查持仓紧急度并生成自动卖出信号

        当持仓技术面严重恶化（紧急度≥阈值）时，主动生成卖出信号

        Args:
            quotes: 实时行情列表
            account: 账户信息

        Returns:
            卖出信号列表
        """
        urgent_signals = []

        try:
            # 检查是否启用紧急卖出
            if not self.urgent_sell_enabled:
                logger.debug("  ⏭️  紧急卖出未启用，跳过检查")
                return []

            # 获取持仓
            positions = account.get("positions", [])
            if not positions:
                logger.debug("  ℹ️  当前无持仓，跳过紧急卖出检查")
                return []

            # 构建行情字典
            quote_dict = {q.symbol: q for q in quotes}

            # 当前时间戳（秒）
            now_ts = datetime.now().timestamp()

            logger.info(f"🚨 检查 {len(positions)} 个持仓的紧急度...")

            for pos in positions:
                symbol = pos.get("symbol")
                try:
                    # 检查冷却期
                    last_check = self.urgent_sell_last_check.get(symbol, 0)
                    if (now_ts - last_check) < self.urgent_sell_cooldown:
                        logger.debug(f"    {symbol}: 冷却期内，跳过")
                        continue

                    # 检查是否已有卖出订单
                    if symbol in self.sold_today:
                        logger.debug(f"    {symbol}: 今日已卖出，跳过")
                        continue

                    # 获取当前价格
                    quote = quote_dict.get(symbol)
                    if not quote or not quote.last_done:
                        logger.debug(f"    {symbol}: 无行情数据，跳过")
                        continue

                    current_price = float(quote.last_done)
                    if current_price <= 0:
                        logger.debug(f"    {symbol}: 价格无效，跳过")
                        continue

                    # 分析持仓技术面
                    tech_analysis = await self._analyze_position_technical(symbol, current_price)

                    # 更新检查时间
                    self.urgent_sell_last_check[symbol] = now_ts

                    # 检查紧急度
                    urgency_score = tech_analysis.get('score', 0)
                    action = tech_analysis.get('action', 'HOLD')

                    if urgency_score >= self.urgent_sell_threshold:
                        logger.warning(
                            f"  🚨 {symbol}: 紧急度 {urgency_score} 分≥阈值 {self.urgent_sell_threshold}，"
                            f"建议={action}"
                        )

                        # 获取持仓信息
                        quantity = pos.get('quantity', 0)
                        if quantity <= 0:
                            logger.warning(f"    {symbol}: 持仓数量无效 ({quantity})，跳过")
                            continue

                        # 构建紧急卖出信号
                        sell_signals = tech_analysis.get('signals', [])
                        reason = f"技术面恶化（紧急度{urgency_score}分）: {', '.join(sell_signals[:3])}"

                        urgent_signal = {
                            'symbol': symbol,
                            'type': 'URGENT_SELL',  # 标记为紧急卖出
                            'side': 'SELL',
                            'price': current_price,
                            'quantity': quantity,
                            'reason': reason,
                            'score': 95,  # 紧急卖出优先级很高
                            'priority': 95,
                            'timestamp': datetime.now(self.beijing_tz).isoformat(),
                            'metadata': {
                                'urgency_score': urgency_score,
                                'technical_signals': sell_signals,
                                'auto_urgent_sell': True
                            }
                        }

                        urgent_signals.append(urgent_signal)

                        logger.success(
                            f"    ✅ {symbol}: 生成紧急卖出信号 "
                            f"(数量={quantity}, 紧急度={urgency_score})"
                        )
                    else:
                        logger.debug(
                            f"    {symbol}: 紧急度 {urgency_score} 分 < 阈值 {self.urgent_sell_threshold}，继续持有"
                        )

                except Exception as e:
                    logger.debug(f"    {symbol}: 紧急度检查失败 - {e}")
                    continue

            # 发送通知
            if urgent_signals and hasattr(self, 'slack') and self.slack:
                notification_lines = [
                    f"🚨 **紧急卖出触发**",
                    f"",
                    f"以下持仓技术面严重恶化，建议立即卖出：",
                    f""
                ]

                for i, signal in enumerate(urgent_signals[:5], 1):
                    metadata = signal.get('metadata', {})
                    urgency = metadata.get('urgency_score', 0)
                    signals_list = metadata.get('technical_signals', [])

                    notification_lines.append(
                        f"{i}. **{signal['symbol']}** - ${signal['price']:.2f}"
                    )
                    notification_lines.append(
                        f"   紧急度: {urgency}分 | 数量: {signal['quantity']}"
                    )
                    notification_lines.append(
                        f"   原因: {', '.join(signals_list[:2])}"
                    )

                if len(urgent_signals) > 5:
                    notification_lines.append(f"   ... 还有 {len(urgent_signals)-5} 个")

                notification_lines.extend([
                    f"",
                    f"⏰ 触发时间: {datetime.now(self.beijing_tz).strftime('%H:%M:%S')}"
                ])

                try:
                    await self.slack.send("\n".join(notification_lines))
                    logger.info("  ✅ 紧急卖出通知已发送")
                except Exception as e:
                    logger.warning(f"  ⚠️  发送通知失败: {e}")

            return urgent_signals

        except Exception as e:
            logger.error(f"❌ 紧急卖出检查失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return []

    async def _rotation_checker_loop(self):
        """
        实时挪仓和紧急卖出后台检查任务
        每30秒检查一次，独立于主循环运行
        """
        logger.info("🔄 启动实时挪仓和紧急卖出后台检查任务（间隔: 30秒）")

        while True:
            try:
                await asyncio.sleep(self._rotation_check_interval)

                # 只在交易时间内检查
                now = datetime.now(self.beijing_tz)

                # 检查是否有任何市场开盘
                hk_open = self._is_market_open_time('HK')
                us_open = self._is_market_open_time('US')

                if not (hk_open or us_open):
                    logger.debug("⏭️  所有市场休市，跳过实时挪仓检查")
                    continue

                # 获取账户信息和实时行情
                try:
                    account = await self.trade_client.get_account()
                except Exception as e:
                    logger.debug(f"⏭️  无法获取账户信息，跳过实时挪仓检查: {e}")
                    account = None

                if not account:
                    continue

                # 获取所有持仓的实时行情
                positions = account.get('positions', [])
                if positions:
                    symbols = [p['symbol'] for p in positions]
                    quotes = await self.quote_client.get_realtime_quote(symbols)
                else:
                    quotes = []

                cash_total = sum(float(v) if isinstance(v, (int, float)) else 0 for v in account.get('cash', {}).values()) if isinstance(account.get('cash'), dict) else float(account.get('cash', 0))
                logger.debug(f"🔍 后台检查: 账户余额=${cash_total:,.2f}, 持仓数={len(positions)}")

                # 1. 实时挪仓检查
                rotation_signals = []
                if getattr(self.settings, 'realtime_rotation_enabled', True):
                    rotation_signals = await self.check_realtime_rotation(
                        quotes=quotes,
                        account=account,
                        regime=getattr(self, 'current_regime', 'RANGE')
                    )

                    if rotation_signals:
                        logger.info(f"🔔 后台检查触发实时挪仓: 生成 {len(rotation_signals)} 个卖出信号")
                        # 发布到信号队列
                        for signal in rotation_signals:
                            await self.signal_queue.publish_signal(signal)

                # 2. 紧急卖出检查
                urgent_signals = []
                if getattr(self.settings, 'urgent_sell_enabled', True):
                    urgent_signals = await self.check_urgent_sells(
                        quotes=quotes,
                        account=account
                    )

                    if urgent_signals:
                        logger.info(f"🚨 后台检查触发紧急卖出: 生成 {len(urgent_signals)} 个卖出信号")
                        # 发布到信号队列
                        for signal in urgent_signals:
                            await self.signal_queue.publish_signal(signal)

                if rotation_signals or urgent_signals:
                    logger.success(
                        f"✅ 后台检查完成: 实时挪仓={len(rotation_signals)}, "
                        f"紧急卖出={len(urgent_signals)}"
                    )

            except asyncio.CancelledError:
                logger.info("🛑 实时挪仓后台任务已停止")
                break
            except Exception as e:
                logger.error(f"❌ 实时挪仓后台检查失败: {e}")
                import traceback
                logger.debug(traceback.format_exc())
                # 继续运行，不中断

    def _is_market_open_time(self, market: str) -> bool:
        """
        检查指定市场是否在交易时间

        Args:
            market: 市场代码 ('HK', 'US', 'SH', 'SZ')

        Returns:
            bool: 是否在交易时间
        """
        now = datetime.now(self.beijing_tz)
        current_time = now.time()
        weekday = now.weekday()

        # 周末不交易
        if weekday >= 5:
            return False

        if market == 'HK':
            # 港股: 9:30-12:00, 13:00-16:00 (16:00收盘竞价，实际交易截止15:00)
            morning = datetime.strptime("09:30", "%H:%M").time() <= current_time <= datetime.strptime("12:00", "%H:%M").time()
            afternoon = datetime.strptime("13:00", "%H:%M").time() <= current_time <= datetime.strptime("15:00", "%H:%M").time()
            return morning or afternoon
        elif market == 'US':
            # 美股: 21:30-次日4:00 (夏令时) 或 22:30-次日5:00 (冬令时)
            # 简化处理：21:00-次日6:00
            return current_time >= datetime.strptime("21:00", "%H:%M").time() or current_time <= datetime.strptime("06:00", "%H:%M").time()
        elif market in ['SH', 'SZ']:
            # A股: 9:30-11:30, 13:00-15:00
            morning = datetime.strptime("09:30", "%H:%M").time() <= current_time <= datetime.strptime("11:30", "%H:%M").time()
            afternoon = datetime.strptime("13:00", "%H:%M").time() <= current_time <= datetime.strptime("15:00", "%H:%M").time()
            return morning or afternoon

        return False

    def _is_us_premarket(self, symbol: str) -> tuple[bool, str]:
        """
        检查美股是否在盘前时段

        Args:
            symbol: 股票代码

        Returns:
            tuple[bool, str]: (是否盘前, 会话类型)
                会话类型: 'pre_market', 'regular', 'after_hours', 'closed'
        """
        if not symbol.endswith('.US'):
            return False, 'n/a'

        now = datetime.now(self.beijing_tz)
        current_time = now.time()
        weekday = now.weekday()

        # 周末不交易
        if weekday >= 5:
            return False, 'closed'

        # 美股盘前时段：16:00-21:30 北京时间 (对应美东 04:00-09:30)
        premarket_start = datetime.strptime("16:00", "%H:%M").time()
        premarket_end = datetime.strptime("21:30", "%H:%M").time()

        # 美股常规交易：21:30-次日04:00 北京时间 (对应美东 09:30-16:00)
        regular_start = datetime.strptime("21:30", "%H:%M").time()
        regular_end = datetime.strptime("04:00", "%H:%M").time()

        # 美股盘后时段：04:00-08:00 北京时间 (对应美东 16:00-20:00)
        afterhours_start = datetime.strptime("04:00", "%H:%M").time()
        afterhours_end = datetime.strptime("08:00", "%H:%M").time()

        # 判断时段
        if premarket_start <= current_time < premarket_end:
            return True, 'pre_market'
        elif current_time >= regular_start or current_time < regular_end:
            return False, 'regular'
        elif afterhours_start <= current_time < afterhours_end:
            return False, 'after_hours'
        else:
            return False, 'closed'


async def main(account_id: str | None = None):
    """
    主函数

    Args:
        account_id: 账号ID，如果指定则从configs/accounts/{account_id}.env加载配置
    """
    generator = SignalGenerator(use_builtin_watchlist=True, account_id=account_id)

    try:
        await generator.run()
    except Exception as e:
        logger.error(f"❌ 信号生成器运行失败: {e}")
        import traceback
        logger.error(traceback.format_exc())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="信号生成器 (Signal Generator) - 扫描市场并生成交易信号",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用默认配置（.env文件）
  python3 scripts/signal_generator.py

  # 使用指定账号配置
  python3 scripts/signal_generator.py --account-id paper_001
  python3 scripts/signal_generator.py --account-id live_001
        """
    )
    parser.add_argument(
        "--account-id",
        type=str,
        default=None,
        help="账号ID（如 paper_001 或 live_001），将从 configs/accounts/{account_id}.env 加载配置"
    )
    args = parser.parse_args()

    print("""
╔══════════════════════════════════════════════════════════════╗
║               信号生成器 (Signal Generator)                   ║
╠══════════════════════════════════════════════════════════════╣
║  功能:                                                         ║
║  • 扫描市场并分析技术指标                                     ║
║  • 生成买入/卖出信号                                          ║
║  • 将信号发送到Redis队列（不执行订单）                        ║
║  • 检查止损止盈条件                                           ║
╚══════════════════════════════════════════════════════════════╝
    """)

    if args.account_id:
        print(f"📌 使用账号配置: {args.account_id}")
        print(f"📁 配置文件: configs/accounts/{args.account_id}.env\n")
    else:
        print(f"📌 使用默认配置: .env\n")

    asyncio.run(main(account_id=args.account_id))
