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
            "9618.HK": {"name": "京东集团-SW", "sector": "平台互联网"},
            "9888.HK": {"name": "百度集团-SW", "sector": "平台互联网"},
            "1024.HK": {"name": "快手-W", "sector": "平台互联网"},
            "9999.HK": {"name": "网易-S", "sector": "平台互联网"},

            # === 半导体/光学（6个）===
            "0981.HK": {"name": "中芯国际", "sector": "半导体"},
            "1347.HK": {"name": "华虹半导体", "sector": "半导体"},
            "2382.HK": {"name": "舜宇光学科技", "sector": "光学"},
            "3888.HK": {"name": "金山软件", "sector": "软件"},
            "0268.HK": {"name": "金蝶国际", "sector": "软件"},
            "0992.HK": {"name": "联想集团", "sector": "硬件"},

            # === 新能源智能车（4个）===
            "1211.HK": {"name": "比亚迪股份", "sector": "新能源汽车"},
            "2015.HK": {"name": "理想汽车-W", "sector": "新能源汽车"},
            "9868.HK": {"name": "小鹏汽车-W", "sector": "新能源汽车"},
            "9866.HK": {"name": "蔚来-SW", "sector": "新能源汽车"},
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
            "PLTR.US": {"name": "Palantir", "sector": "AI"},
            # 电商 & 金融科技
            "SHOP.US": {"name": "Shopify", "sector": "电商"},
            # ETF指数基金
            "QQQ.US": {"name": "纳指100ETF", "sector": "ETF"},
            # 杠杆ETF
            "TQQQ.US": {"name": "纳指三倍做多ETF", "sector": "ETF"},
            "NVDU.US": {"name": "英伟达二倍做多ETF", "sector": "ETF"},
            # 其他
            "RKLB.US": {"name": "火箭实验室", "sector": "航天"},
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

        # 今日已交易标的集合（避免重复下单）
        self.traded_today = set()  # 今日买单标的（包括pending）
        self.sold_today = set()     # 今日卖单标的（包括pending）- 新增
        self.current_positions = set()  # 当前持仓标的（内存缓存，从Redis同步）

        # 信号生成历史（防止重复信号）
        self.signal_history = {}  # {symbol: last_signal_time}
        self.signal_cooldown = 900  # 信号冷却期（秒），15分钟内不重复生成同一标的的信号（修复：从5分钟延长到15分钟）

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

        # === BUY信号的去重检查 ===
        if signal_type in ["BUY", "STRONG_BUY", "WEAK_BUY"]:
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

        # === SELL信号的去重检查 ===
        elif signal_type in ["SELL", "STOP_LOSS", "TAKE_PROFIT", "SMART_TAKE_PROFIT", "EARLY_TAKE_PROFIT"]:
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
        1. 检查持仓的止损止盈（最高优先级）
        2. 分析新的买入信号（防抖：价格变化>0.5%才计算）
        """
        try:
            current_price = float(quote.last_done)
            if current_price <= 0:
                return

            # 防抖：判断是否需要重新计算
            if not self._should_recalculate(symbol, current_price):
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

            # 2. 获取止损止盈设置（从数据库）
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

        except Exception as e:
            logger.warning(f"⚠️ 动态订阅失败: {e}")

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

            # 使用async with正确初始化客户端
            async with QuoteDataClient(self.settings) as quote_client, \
                       LongportTradingClient(self.settings) as trade_client:

                # 保存客户端引用
                self.quote_client = quote_client
                self.trade_client = trade_client

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

                logger.info(f"📋 监控标的数量: {len(all_symbols)}")
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

                        # 5. 检查现有持仓的止损止盈（生成平仓信号）
                        try:
                            if account:
                                exit_signals = await self.check_exit_signals(quotes, account)
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

                        # 5. 显示本轮统计
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
                'macd': np.nan, 'macd_signal': np.nan, 'macd_histogram': np.nan,
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

            # 计算止损止盈
            atr = ind.get('atr', 0)
            if atr and atr > 0:
                stop_loss = current_price - (2.5 * atr)
                take_profit = current_price + (3.5 * atr)
            else:
                stop_loss = current_price * 0.95
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
            # 获取历史K线数据
            end_date = datetime.now()
            days_to_fetch = 100
            start_date = end_date - timedelta(days=days_to_fetch)

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
        stops: Dict
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

        # 1. MACD死叉（+50分）- 最强卖出信号
        if prev_macd_histogram > 0 > macd_histogram:
            score += 50
            reasons.append("⚠️ MACD死叉")

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

        # 根据评分决定动作
        if score >= 50:
            action = "TAKE_PROFIT_NOW"
            adjusted_take_profit = current_price  # 立即止盈
        elif score >= 30:
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

        # 止损位调整（根据趋势和ATR）
        atr = indicators.get('atr', 0)
        if atr and atr > 0:
            # 使用ATR动态调整止损
            if action in ["STRONG_HOLD", "DELAY_TAKE_PROFIT"]:
                # 持有信号：放宽止损
                adjusted_stop_loss = current_price - (3.0 * atr)
            else:
                adjusted_stop_loss = current_price - (2.5 * atr)
        else:
            # 固定百分比止损
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

    async def check_exit_signals(self, quotes, account):
        """
        检查现有持仓的止损止盈条件（智能版 - 基于技术指标）

        增强功能:
        1. 获取技术指标（RSI, MACD, 布林带, SMA等）
        2. 计算智能退出评分
        3. 根据指标决定是否延迟止盈或提前止损
        4. 保留固定止损止盈作为保底逻辑
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
                        stops=stops
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
                        })

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
                    })

                # 检查固定止盈（仅在没有智能决策或决策为STANDARD时）
                elif stops.get('take_profit') and current_price >= stops['take_profit']:
                    # 如果有指标分析且建议持有，则不执行固定止盈
                    if indicators:
                        exit_decision = self._calculate_exit_score(
                            indicators, position, current_price, stops
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
                    })

        except Exception as e:
            logger.error(f"❌ 检查退出信号失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())

        return exit_signals


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
