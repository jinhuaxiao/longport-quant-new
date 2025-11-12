#!/usr/bin/env python3
"""
订单执行器 - 负责从队列消费信号并执行订单

职责：
1. 从Redis队列消费交易信号
2. 执行风控检查（资金、持仓、限制）
3. 计算订单数量和价格
4. 提交订单到LongPort
5. 更新数据库和发送通知
6. 处理失败和重试

与原 advanced_technical_trading.py 的区别：
- 不负责信号生成，只消费队列中的信号
- 专注于订单执行和风控
- 支持并发执行（可启动多个实例）

"""

import asyncio
import sys
import time
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from pathlib import Path
from loguru import logger
from typing import Dict, Optional, List

# 添加项目根目录到路径
sys.path.append(str(Path(__file__).parent.parent))

from longport import openapi
from longport_quant.config import get_settings
from longport_quant.execution.client import LongportTradingClient
from longport_quant.execution.smart_router import SmartOrderRouter, OrderRequest, ExecutionStrategy
from longport_quant.execution.risk_assessor import RiskAssessor
from longport_quant.risk.regime import RegimeClassifier
from longport_quant.risk.rebalancer import RegimeRebalancer
from longport_quant.risk.kelly import KellyCalculator
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.messaging import SignalQueue
from longport_quant.notifications import MultiChannelNotifier
from longport_quant.utils import LotSizeHelper
from longport_quant.persistence.order_manager import OrderManager
from longport_quant.persistence.stop_manager import StopLossManager
from longport_quant.persistence.position_manager import RedisPositionManager
from longport_quant.persistence.db import DatabaseSessionManager
from datetime import datetime


class InsufficientFundsError(Exception):
    """资金不足异常"""
    pass


class OrderExecutor:
    """订单执行器（从队列消费信号并执行）"""

    def __init__(self, account_id: str | None = None):
        """
        初始化订单执行器

        Args:
            account_id: 账号ID，如果指定则从configs/accounts/{account_id}.env加载配置
        """
        self.settings = get_settings(account_id=account_id)
        self.account_id = account_id or "default"
        self.beijing_tz = ZoneInfo('Asia/Shanghai')

        # 初始化消息队列
        self.signal_queue = SignalQueue(
            redis_url=self.settings.redis_url,
            queue_key=self.settings.signal_queue_key,
            processing_key=self.settings.signal_processing_key,
            failed_key=self.settings.signal_failed_key,
            max_retries=self.settings.signal_max_retries
        )

        # 交易参数
        self.max_positions = 999  # 不限制持仓数量（实际受资金限制）
        self.max_positions_by_market = {
            'HK': 8,   # 港股最多8个
            'US': 5,   # 美股最多5个
            'SH': 2,   # A股上交所最多2个
            'SZ': 2,   # A股深交所最多2个
        }
        self.min_position_size_pct = 0.05  # 最小仓位5%
        self.max_position_size_pct = 0.25  # 最大仓位25%（优化后，从40%降低）
        self.min_cash_reserve = 1000  # 最低现金储备
        self.use_adaptive_budget = True  # 启用自适应预算

        # 分批建仓配置
        self.enable_staged_entry = False  # 是否启用分批建仓（默认关闭，一次性建仓）
        self.stage_interval_minutes = 15  # 批次间隔（分钟）

        # 组件（延迟初始化）
        self.trade_client = None
        self.quote_client = None
        self.slack = None
        self.smart_router = None  # SmartOrderRouter for TWAP/VWAP execution
        self.lot_size_helper = LotSizeHelper()
        self.order_manager = OrderManager()
        self.stop_manager = StopLossManager()

        # 【新增】Kelly 公式计算器 - 基于历史胜率动态调整仓位
        self.kelly_calculator = KellyCalculator(self.settings)

        # 【新增】风险评估器 - 智能决策备份条件单
        self.risk_assessor = RiskAssessor(config=self.settings.backup_orders)

        # 【新增】Redis持仓管理器 - 跨进程共享持仓状态
        self.position_manager = RedisPositionManager(
            redis_url=self.settings.redis_url,
            key_prefix="trading"
        )

        # 持仓追踪
        self.positions_with_stops = {}  # {symbol: {entry_price, stop_loss, take_profit}}

        # 【新增】账户信息缓存（避免API限流）
        self._account_cache = None
        self._account_cache_time = None
        self._account_cache_ttl = 30  # 缓存30秒

        # 【新增】市场状态（Regime）管理
        self.current_regime = "RANGE"
        self._regime_task = None
        self.regime_classifier = RegimeClassifier(self.settings)
        self._last_regime_notified: str | None = None
        self._last_regime_summary_day: str | None = None
        # 日内风格
        self.current_intraday_style = "RANGE"  # 'TREND' | 'RANGE'
        self._intraday_task = None
        self._last_intraday_notified: str | None = None
        # 去杠杆调仓
        self._rebalancer_task = None
        self.rebalancer = RegimeRebalancer(account_id=self.account_id)

        # 🔄 港股收盘前强制轮换配置（用于轮换分析）
        self.hk_force_rotation_enabled = bool(getattr(self.settings, 'hk_force_rotation_enabled', False))
        self.hk_force_rotation_max = int(getattr(self.settings, 'hk_force_rotation_max', 2))

    async def run(self):
        """主循环：消费信号并执行订单"""
        logger.info("=" * 70)
        logger.info("🚀 订单执行器启动")
        logger.info("=" * 70)

        try:
            # 使用async with正确初始化客户端
            async with QuoteDataClient(self.settings) as quote_client, \
                       LongportTradingClient(self.settings) as trade_client:

                # 保存客户端引用
                self.quote_client = quote_client
                self.trade_client = trade_client

                # 初始化通知（支持Slack和Discord）
                slack_url = str(self.settings.slack_webhook_url) if self.settings.slack_webhook_url else None
                discord_url = str(self.settings.discord_webhook_url) if self.settings.discord_webhook_url else None
                self.slack = MultiChannelNotifier(slack_webhook_url=slack_url, discord_webhook_url=discord_url)

                # 🔥 连接Redis持仓管理器
                await self.position_manager.connect()
                logger.info("✅ Redis持仓管理器已连接")

                # 🔥 初始化SmartOrderRouter（用于TWAP/VWAP算法订单）
                db_manager = DatabaseSessionManager(self.settings.database_dsn, auto_init=True)
                trade_ctx = await trade_client.get_trade_context()
                self.smart_router = SmartOrderRouter(trade_ctx, db_manager, quote_client=quote_client, settings=self.settings)
                logger.info("✅ SmartOrderRouter已初始化（支持TWAP/VWAP算法订单，使用QuoteClient获取手数）")

                # 🔥 启动Regime状态更新任务（可选）
                if getattr(self.settings, 'regime_enabled', False):
                    try:
                        self._regime_task = asyncio.create_task(self._regime_updater())
                        logger.info("✅ Regime状态机已启动")
                    except Exception as e:
                        logger.warning(f"⚠️ 启动Regime任务失败: {e}")
                if getattr(self.settings, 'intraday_style_enabled', False):
                    try:
                        self._intraday_task = asyncio.create_task(self._intraday_style_updater())
                        logger.info("✅ 日内风格检测已启动")
                    except Exception as e:
                        logger.warning(f"⚠️ 启动日内风格任务失败: {e}")
                if getattr(self.settings, 'rebalancer_enabled', False):
                    try:
                        self._rebalancer_task = asyncio.create_task(self._rebalancer_updater())
                        logger.info("✅ 去杠杆调仓器已启动")
                    except Exception as e:
                        logger.warning(f"⚠️ 启动去杠杆任务失败: {e}")

                # 🔥 启动队列状态通知任务（每小时汇报）
                try:
                    self._queue_status_task = asyncio.create_task(self._queue_status_notifier())
                    logger.info("✅ 队列状态通知已启动（每小时汇报）")
                except Exception as e:
                    logger.warning(f"⚠️ 启动队列状态通知失败: {e}")

                # 🔥 启动延迟信号清理任务（每10分钟）
                try:
                    self._delayed_signal_cleaner_task = asyncio.create_task(self._delayed_signal_cleaner())
                    logger.info("✅ 延迟信号清理已启动（每10分钟自动清理超时信号）")
                except Exception as e:
                    logger.warning(f"⚠️ 启动延迟信号清理失败: {e}")

                logger.info("✅ 订单执行器初始化完成")

                # 启动时恢复所有僵尸信号
                logger.info("🔧 检查并恢复僵尸信号...")
                try:
                    recovered_count = await self.signal_queue.recover_zombie_signals(timeout_seconds=0)
                    if recovered_count > 0:
                        logger.warning(f"⚠️ 发现并恢复了 {recovered_count} 个卡住的信号")
                    else:
                        logger.info("✅ 没有需要恢复的信号")
                except Exception as e:
                    logger.warning(f"⚠️ 恢复僵尸信号时出错: {e}")

                logger.info(f"📥 开始监听信号队列: {self.settings.signal_queue_key}")
                logger.info(f"🔄 最大重试次数: {self.settings.signal_max_retries}")
                logger.info(f"🎯 批量处理模式: 窗口={self.settings.signal_batch_window}秒, 批大小={self.settings.signal_batch_size}")
                logger.info(f"📊 智能优先级: 高分信号优先，止损信号立即执行")
                logger.info("")

                while True:
                    try:
                        # 【新批量模式】收集一批信号
                        batch = await self._consume_batch()

                        if not batch:
                            # 🔥 批次为空，使用配置的休眠时间避免CPU空转
                            sleep_time = self.settings.empty_queue_sleep
                            logger.debug(f"  💤 队列为空或只有延迟信号，休眠{sleep_time}秒...")
                            await asyncio.sleep(sleep_time)
                            continue

                        logger.info(f"\n{'='*70}")
                        logger.info(f"🚀 开始处理批次: {len(batch)}个信号")
                        logger.info(f"{'='*70}\n")

                        # 处理批次中的每个信号（按score降序）
                        remaining_signals = []
                        funds_exhausted = False

                        for idx, signal in enumerate(batch, 1):
                            symbol = signal.get('symbol')
                            signal_type = signal.get('type')
                            score = signal.get('score', 0)

                            logger.info(f"\n--- [{idx}/{len(batch)}] 处理信号: {symbol} ---")
                            logger.info(f"  类型={signal_type}, 评分={score}")

                            # 执行订单（带超时保护）
                            try:
                                # 60秒超时保护
                                await asyncio.wait_for(
                                    self.execute_order(signal),
                                    timeout=60.0
                                )

                                # 标记信号处理完成
                                await self.signal_queue.mark_signal_completed(signal)
                                logger.success(f"  ✅ [{idx}/{len(batch)}] {symbol} 处理完成")

                            except asyncio.TimeoutError:
                                error_msg = "订单执行超时（60秒）"
                                logger.error(f"  ❌ {error_msg}: {symbol}")

                                # 标记信号失败（会自动重试）
                                await self.signal_queue.mark_signal_failed(
                                    signal,
                                    error_message=error_msg,
                                    retry=True
                                )

                            except InsufficientFundsError as e:
                                # 资金不足：只延迟当前信号，继续处理后续信号（可能需要更少资金）
                                error_detail = str(e)
                                logger.warning(f"  ⚠️ [{idx}/{len(batch)}] {symbol}: 资金不足")
                                logger.info(f"  📋 详细原因:\n{error_detail}")
                                logger.info(f"  💡 策略：仅延迟当前信号，继续处理后续{len(batch)-idx}个信号")

                                # 🔥 检查重试次数，避免无限重试
                                retry_count = signal.get('retry_count', 0)
                                max_funds_retries = 3  # 资金不足最多重试3次

                                if retry_count >= max_funds_retries:
                                    logger.warning(
                                        f"  ⚠️ {symbol}: 资金不足已重试{retry_count}次，停止重试\n"
                                        f"     建议: 等待资金充足后手动处理，或优化持仓释放资金"
                                    )
                                    # 标记为失败，不再重试
                                    await self.signal_queue.mark_signal_failed(
                                        signal,
                                        error_message=f"资金不足重试{retry_count}次后放弃",
                                        retry=False  # 不再重试
                                    )

                                    # 发送最终放弃的通知
                                    try:
                                        await self._send_insufficient_funds_final_notification(
                                            signal=signal,
                                            retry_count=retry_count,
                                            error_detail=error_detail
                                        )
                                    except Exception as notify_err:
                                        logger.warning(f"  ⚠️ 发送通知失败: {notify_err}")
                                else:
                                    # 还可以重试
                                    # 注释掉单独通知，避免与批次汇总通知重复
                                    # 批次处理完成后会统一发送汇总通知，信息更简洁
                                    # try:
                                    #     await self._send_insufficient_funds_notification(
                                    #         signal=signal,
                                    #         error_detail=error_detail
                                    #     )
                                    # except Exception as notify_err:
                                    #     logger.warning(f"  ⚠️ 发送资金不足通知失败: {notify_err}")

                                    # 只将当前信号加入待重新入队列表
                                    remaining_signals.append(signal)

                                # 标记此信号为资金不足（用于统计）
                                funds_exhausted = True
                                # 不break，继续处理后续信号

                            except Exception as e:
                                error_msg = f"{type(e).__name__}: {str(e)}"
                                logger.error(f"  ❌ 执行订单失败: {error_msg}")

                                # 标记信号失败（会自动重试）
                                await self.signal_queue.mark_signal_failed(
                                    signal,
                                    error_message=error_msg,
                                    retry=True
                                )

                        # 批次处理完成后的统计
                        logger.info(f"\n{'='*70}")
                        if remaining_signals:
                            logger.warning(f"⚠️ 批次处理完成: 部分信号资金不足")
                            logger.info(f"  已处理: {len(batch)}个信号")
                            logger.info(f"  成功/失败: {len(batch)-len(remaining_signals)}/{len(remaining_signals)}个")
                            logger.info(f"  待重试: {len(remaining_signals)}个信号（资金不足）")

                            # 重新入队资金不足的信号
                            requeued = await self._requeue_remaining(
                                remaining_signals,
                                reason="资金不足"
                            )
                            logger.info(f"  ✅ 已重新入队: {requeued}个信号")
                        else:
                            logger.success(f"✅ 批次处理完成: {len(batch)}/{len(batch)}个信号全部成功")

                        logger.info(f"{'='*70}\n")

                    except asyncio.CancelledError:
                        logger.info("⚠️ 收到取消信号，正在退出...")
                        break
                    except Exception as e:
                        logger.error(f"❌ 消费循环出错: {e}")
                        import traceback
                        logger.debug(traceback.format_exc())
                        await asyncio.sleep(5)  # 错误后等待5秒

        except KeyboardInterrupt:
            logger.info("\n⚠️ 收到中断信号，正在退出...")
        finally:
            # 关闭Redis连接
            await self.signal_queue.close()
            await self.position_manager.close()
            logger.info("✅ 资源清理完成")

    async def _get_account_with_cache(self, force_refresh: bool = False) -> Dict:
        """
        获取账户信息（带缓存，避免API限流）

        Args:
            force_refresh: 是否强制刷新缓存

        Returns:
            账户信息字典
        """
        from datetime import datetime, timedelta

        now = datetime.now()

        # 检查缓存是否有效
        if not force_refresh and self._account_cache is not None and self._account_cache_time is not None:
            cache_age = (now - self._account_cache_time).total_seconds()
            if cache_age < self._account_cache_ttl:
                logger.debug(f"  📦 使用账户信息缓存（{cache_age:.1f}秒前）")
                return self._account_cache

        # 缓存失效或强制刷新，重新获取
        try:
            logger.debug(f"  🔄 刷新账户信息缓存...")
            account = await self.trade_client.get_account()
            self._account_cache = account
            self._account_cache_time = now
            logger.debug(f"  ✅ 账户信息已缓存（TTL={self._account_cache_ttl}秒）")
            return account
        except Exception as e:
            logger.warning(f"  ⚠️ 刷新账户信息失败: {e}")
            # 如果有旧缓存，降级使用
            if self._account_cache is not None:
                logger.warning(f"  ⚠️ 降级使用旧缓存")
                return self._account_cache
            raise

    async def execute_order(self, signal: Dict):
        """
        执行订单（核心逻辑）

        Args:
            signal: 信号数据，包含symbol, type, score等
        """
        symbol = signal['symbol']
        signal_type = signal['type']
        side = signal.get('side', 'BUY')
        score = signal.get('score', 0)
        current_price = signal.get('price', 0)

        logger.info(f"🔍 开始处理 {symbol} 的 {signal_type} 信号")

        # 1. 区分买入和卖出
        if side == 'BUY':
            await self._execute_buy_order(signal)
        elif side == 'SELL':
            await self._execute_sell_order(signal)
        else:
            logger.error(f"❌ 未知的订单方向: {side}")

    async def _analyze_position_for_rotation(
        self,
        position: Dict,
        new_signal_score: int
    ) -> Dict:
        """
        分析持仓是否适合轮换

        Args:
            position: 持仓信息
            new_signal_score: 新信号评分

        Returns:
            持仓分析结果
        """
        symbol = position.get('symbol', '')
        quantity = float(position.get('quantity', 0))
        cost_price = float(position.get('cost_price', 0))

        # 获取当前市价
        try:
            quote = await self.quote_client.get_quote(symbol)
            current_price = float(quote.last_done) if quote and quote.last_done else cost_price
        except Exception:
            current_price = cost_price

        # 计算盈亏
        market_value = quantity * current_price
        cost_value = quantity * cost_price
        pnl = market_value - cost_value
        pnl_pct = (pnl / cost_value * 100) if cost_value > 0 else 0

        # 计算持有时间
        entry_time = position.get('entry_time')
        hold_hours = 0
        if entry_time:
            try:
                from datetime import datetime
                from zoneinfo import ZoneInfo
                beijing_tz = ZoneInfo('Asia/Shanghai')
                now = datetime.now(beijing_tz)
                if isinstance(entry_time, str):
                    entry_dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
                else:
                    entry_dt = entry_time
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.replace(tzinfo=beijing_tz)
                hold_hours = (now - entry_dt).total_seconds() / 3600
            except Exception:
                pass

        # 计算轮换评分（0-100，越低越适合卖出）
        rotation_score = 50  # 基准分

        # 1. 盈亏影响（-50 to +30）
        if pnl_pct < -10:  # 亏损超过10%
            rotation_score -= 30  # 优先卖出止损
        elif pnl_pct < -5:  # 亏损5-10%
            rotation_score -= 20
        elif pnl_pct < 0:  # 小幅亏损
            rotation_score -= 10
        elif pnl_pct > 20:  # 盈利超过20%
            rotation_score += 30  # 保留高盈利
        elif pnl_pct > 10:  # 盈利10-20%
            rotation_score += 20
        elif pnl_pct > 5:  # 盈利5-10%
            rotation_score += 10

        # 2. 持有时间影响（-10 to +10）
        if hold_hours < 1:  # 持有不到1小时
            rotation_score += 10  # 保留新开仓位
        elif hold_hours > 24:  # 持有超过1天
            rotation_score -= 10  # 优先清理老仓位

        # 3. 与新信号评分对比（-20 to 0）
        # 如果新信号比当前持仓潜力大，降低保留分数
        if new_signal_score > 70:  # 新信号是强信号
            rotation_score -= 20
        elif new_signal_score > 60:  # 新信号是中等信号
            rotation_score -= 10

        # 生成建议
        if rotation_score < 30:
            recommendation = "🔴 强烈建议卖出"
            reason = []
            if pnl_pct < -10:
                reason.append(f"深度亏损{pnl_pct:.1f}%")
            if hold_hours > 24:
                reason.append(f"持有过久({hold_hours:.1f}小时)")
            if new_signal_score > 70:
                reason.append(f"新信号更优({new_signal_score}分)")
        elif rotation_score < 50:
            recommendation = "🟡 可考虑卖出"
            reason = ["表现一般，可为更优信号腾出空间"]
        else:
            recommendation = "🟢 建议保留"
            reason = []
            if pnl_pct > 10:
                reason.append(f"高盈利{pnl_pct:.1f}%")
            if hold_hours < 1:
                reason.append("刚开仓")

        return {
            'symbol': symbol,
            'quantity': quantity,
            'cost_price': cost_price,
            'current_price': current_price,
            'market_value': market_value,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'hold_hours': hold_hours,
            'rotation_score': rotation_score,
            'recommendation': recommendation,
            'reason': ', '.join(reason) if reason else '无特殊原因',
            'potential_freed': market_value
        }

    async def _preflight_check_buying_power(
        self,
        symbol: str,
        current_price: float,
        score: int,
        account: Dict
    ) -> tuple[bool, str, Optional[float]]:
        """
        购买力预检查 - 在执行订单前判断是否有足够资金或可换仓空间

        Args:
            symbol: 股票代码
            current_price: 当前价格
            score: 信号评分
            account: 账户信息

        Returns:
            (是否可以继续, 详细说明, 可用预算)
        """
        currency = "HKD" if ".HK" in symbol else "USD"
        available_cash = float(account["cash"].get(currency, 0))
        buy_power = float(account.get("buy_power", {}).get(currency, 0))
        remaining_finance = float(account.get("remaining_finance", {}).get(currency, 0))

        # 1. 获取手数
        lot_size = await self.lot_size_helper.get_lot_size(symbol, self.quote_client)

        # 2. 计算最小所需资金（买1手）
        min_required_cash = current_price * lot_size

        # 3. 计算动态预算
        signal_dict = {
            'symbol': symbol,
            'price': current_price,
            'score': score,
            'type': 'BUY'
        }
        dynamic_budget = await self._calculate_dynamic_budget(account, signal_dict)

        # 4. 券商端可买数量预估（避免明知无法下单仍然尝试）
        broker_max_qty = await self._estimate_available_quantity(
            symbol=symbol,
            price=current_price,
            lot_size=lot_size,
            currency=None
        )
        broker_allows_purchase = broker_max_qty >= lot_size and broker_max_qty > 0

        # 5. 判断资金是否充足
        has_sufficient_funds = (
            dynamic_budget >= min_required_cash and
            (available_cash >= min_required_cash or remaining_finance >= min_required_cash)
        )

        if has_sufficient_funds and broker_allows_purchase:
            return True, f"资金充足: 预算=${dynamic_budget:.2f}, 最小需要=${min_required_cash:.2f}", dynamic_budget

        logger.info(
            f"  💡 预检查发现资金/额度限制:\n"
            f"     标的: {symbol}\n"
            f"     需要: ${min_required_cash:.2f} (1手 × {lot_size}股 × ${current_price:.2f})\n"
            f"     可用现金: ${available_cash:.2f}\n"
            f"     动态预算: ${dynamic_budget:.2f}\n"
            f"     购买力: ${buy_power:.2f}\n"
            f"     剩余融资: ${remaining_finance:.2f}\n"
            f"     券商可买估算: {broker_max_qty}股"
        )

        if not broker_allows_purchase:
            logger.warning(
                f"  ⚠️ 券商预估可买数量不足: {broker_max_qty}股 < 最小手数{lot_size}股\n"
                f"     可能原因: 购买力受限、融资额度不足或待结算资金占用"
            )

        # 6. 资金缺口 & 是否允许尝试轮换
        shortfall_cash = max(0.0, min_required_cash - available_cash)
        effective_power = max(available_cash, buy_power, remaining_finance)
        shortfall_power = max(0.0, min_required_cash - effective_power)
        needed_amount = max(min_required_cash, shortfall_cash, shortfall_power)
        rotation_allowed = score >= 60

        broker_reason_lines = []
        if not broker_allows_purchase:
            broker_reason_lines.append(
                f"   • 券商预估可买数量为{broker_max_qty}股 (< {lot_size}股)"
            )
            broker_reason_lines.append(
                f"   • 买入力: ${buy_power:.2f}, 剩余融资: ${remaining_finance:.2f}"
            )

        # 7. 获取持仓并进行分析
        try:
            positions = account.get("positions") or []
            if not positions:
                positions = await self.trade_client.get_positions()
        except Exception as e:
            logger.warning(f"  ⚠️ 获取持仓信息失败: {e}")
            positions = []

        # 如果API无法获取，尝试Redis缓存
        if not positions:
            try:
                redis_positions = await self.position_manager.get_all_position_details()
                if redis_positions:
                    positions = [
                        {
                            "symbol": sym,
                            "quantity": float(data.get("quantity", 0)),
                            "cost_price": float(data.get("cost_price", 0)),
                            "current_price": 0.0,  # 稍后通过行情重算
                            "market": "",
                            "currency": "",
                        }
                        for sym, data in redis_positions.items()
                        if float(data.get("quantity", 0)) > 0
                    ]
                    if positions:
                        logger.info(
                            f"  🔄 使用Redis缓存持仓，共{len(positions)}个标的"
                        )
            except Exception as redis_err:
                logger.warning(f"  ⚠️ Redis持仓缓存获取失败: {redis_err}")

        if not positions:
            reason_lines = [
                f"❌ 无法买入 {symbol}:",
                f"   • 资金不足: 需要${min_required_cash:.2f}, 可用${available_cash:.2f}"
            ]
            reason_lines.extend(broker_reason_lines)
            reason_lines.append("   • 无法获取有效持仓信息，暂无法评估换仓")
            reason_lines.append("   💡 建议: 等待资金到账或手动调整持仓")
            return False, "\n".join(reason_lines), None

        if not positions:
            reason_lines = [
                f"❌ 无法买入 {symbol}:",
                f"   • 资金不足: 需要${min_required_cash:.2f}, 可用${available_cash:.2f}"
            ]
            reason_lines.extend(broker_reason_lines)
            if rotation_allowed:
                reason_lines.append("   • 当前无持仓可以换仓")
            else:
                reason_lines.append(f"   • 信号评分过低({score}分 < 60分)，系统不会自动换仓")
            reason_lines.append("   💡 建议: 等待资金到账或市场机会")
            return False, "\n".join(reason_lines), None

        position_analyses = []
        for pos in positions:
            if pos.get('quantity', 0) <= 0:
                continue
            analysis = await self._analyze_position_for_rotation(pos, score)
            position_analyses.append(analysis)

        if not position_analyses:
            reason_lines = [
                f"❌ 无法买入 {symbol}:",
                f"   • 资金不足: 需要${min_required_cash:.2f}, 可用${available_cash:.2f}"
            ]
            reason_lines.extend(broker_reason_lines)
            reason_lines.append("   • 当前无有效持仓可以换仓")
            reason_lines.append("   💡 建议: 等待高质量卖出信号或手动释放资金")
            return False, "\n".join(reason_lines), None

        # 按轮换评分排序（评分越低越适合卖出）
        position_analyses.sort(key=lambda x: x['rotation_score'])

        # 构建持仓摘要（用于错误提示）
        def _summarise_positions(data, limit: int = 3) -> str:
            if not data:
                return "无持仓"
            parts = []
            for item in data[:limit]:
                parts.append(
                    f"{item['symbol']}({item['pnl_pct']:+.1f}%, 市值${item['market_value']:,.0f})"
                )
            if len(data) > limit:
                parts.append(f"…共{len(data)}个")
            return "，".join(parts)

        position_summary = _summarise_positions(position_analyses, limit=4)

        # 找出可以释放足够资金的持仓（用于Slack分析）
        suggested_sales = []
        cumulative_freed = 0.0
        for analysis in position_analyses:
            if analysis['rotation_score'] < 50:
                suggested_sales.append(analysis)
                cumulative_freed += analysis['potential_freed']
                if cumulative_freed >= needed_amount:
                    break

        # 🔥 发送详细的持仓分析到Slack
        await self._send_position_rotation_analysis(
            new_signal={
                'symbol': symbol,
                'price': current_price,
                'score': score
            },
            needed_amount=needed_amount,
            available_cash=available_cash,
            all_positions=position_analyses,
            suggested_sales=suggested_sales,
            rotation_allowed=rotation_allowed
        )

        if rotation_allowed and suggested_sales:
            sales_summary = ", ".join([
                f"{p['symbol']}({p['recommendation']}, 释放${p['potential_freed']:.0f})"
                for p in suggested_sales[:3]
            ])
            reason_lines = [
                "⚠️ 资金/额度不足，建议换仓:",
                f"   • 标的: {symbol} (评分{score}分)",
                f"   • 需要: ${min_required_cash:.2f}, 可用: ${available_cash:.2f}"
            ]
            if not broker_allows_purchase:
                reason_lines.append(
                    f"   • 券商预估可买数量为{broker_max_qty}股 (< {lot_size}股)"
                )
            reason_lines.extend([
                f"   • 建议卖出: {sales_summary}",
                f"   • 可释放: ${cumulative_freed:.2f}",
                f"   • 当前持仓概览: {position_summary}",
                "   💡 详细分析已发送到Slack，请决策是否换仓"
            ])
            return True, "\n".join(reason_lines), dynamic_budget

        # rotation未允许或无足够建议
        cumulative_freed = sum(p['potential_freed'] for p in suggested_sales)
        reason_lines = [
            f"❌ 无法买入 {symbol}:",
            f"   • 资金不足: 需要${min_required_cash:.2f}, 可用${available_cash:.2f}"
        ]
        reason_lines.extend(broker_reason_lines)

        if not rotation_allowed:
            reason_lines.append(f"   • 信号评分过低({score}分 < 60分)，系统不会自动换仓")
            if cumulative_freed > 0:
                reason_lines.append(
                    f"   • 潜在可释放资金: ${cumulative_freed:.2f}（需手动确认）"
                )
            reason_lines.append(f"   • 当前持仓概览: {position_summary}")
        elif suggested_sales:
            reason_lines.append(
                f"   • 建议持仓可释放资金${cumulative_freed:.2f} < 缺口${needed_amount:.2f}"
            )
            reason_lines.append(f"   • 当前持仓概览: {position_summary}")
        else:
            reason_lines.append("   • 当前持仓质量较好，不建议换仓")
            reason_lines.append(f"   • 当前持仓概览: {position_summary}")

        reason_lines.append("   💡 详细分析已发送到Slack，建议手动评估调整")
        return False, "\n".join(reason_lines), None

    async def _execute_buy_order(self, signal: Dict):
        """执行买入订单"""
        symbol = signal['symbol']
        signal_type = signal['type']
        current_price = signal.get('price', 0)
        score = signal.get('score', 0)

        # 1. 获取账户信息（使用缓存）
        try:
            account = await self._get_account_with_cache()
        except Exception as e:
            logger.error(f"❌ 获取账户信息失败: {e}")
            raise

        # 2. 弱买入信号过滤
        if signal_type == "WEAK_BUY" and score < 35:
            logger.info(f"  ⏭️ 跳过弱买入信号 (评分: {score})")
            return  # 直接返回，信号会被标记为完成

        # 3. 🔥 【新增】购买力预检查 - 提前判断资金状况
        can_proceed, check_message, suggested_budget = await self._preflight_check_buying_power(
            symbol=symbol,
            current_price=current_price,
            score=score,
            account=account
        )

        logger.info(f"  💰 购买力预检查结果:\n{check_message}")

        if not can_proceed:
            # 资金不足且无法换仓，直接抛出异常，避免无意义的下单尝试
            raise InsufficientFundsError(check_message)

        # 4. 资金检查（保留原有逻辑以兼容）
        currency = "HKD" if ".HK" in symbol else "USD"
        available_cash = float(account["cash"].get(currency, 0))
        buy_power = float(account.get("buy_power", {}).get(currency, 0))
        remaining_finance = float(account.get("remaining_finance", {}).get(currency, 0))

        # 跨币种债务诊断：检测"有现金但买入力为负"的情况
        if available_cash > 0 and buy_power < 0:
            # 获取所有币种的现金和买入力
            all_cash = account.get("cash", {})
            all_buy_power = account.get("buy_power", {})

            logger.warning(
                f"🔍 跨币种债务诊断 - {currency}:\n"
                f"   {currency}现金: ${available_cash:,.2f} ✅\n"
                f"   {currency}买入力: ${buy_power:,.2f} ❌\n"
                f"   \n"
                f"   📊 全账户状态:\n"
                + "\n".join([
                    f"   • {ccy}: 现金=${float(all_cash.get(ccy, 0)):,.0f}, "
                    f"买入力=${float(all_buy_power.get(ccy, 0)):,.0f}"
                    for ccy in sorted(set(list(all_cash.keys()) + list(all_buy_power.keys())))
                ]) +
                f"\n\n"
                f"   ⚠️ 可能原因:\n"
                f"   • 其他币种融资债务影响整体账户购买力\n"
                f"   • LongPort风控将跨币种债务纳入购买力计算\n"
                f"   \n"
                f"   💡 建议:\n"
                f"   • 系统将尝试Fallback现金估算（使用50%现金）\n"
                f"   • 考虑减仓释放购买力\n"
                f"   • 或归还融资债务恢复购买力"
            )

        # 显示购买力和融资额度信息
        logger.debug(
            f"  💰 {currency} 资金状态 - 可用: ${available_cash:,.2f}, "
            f"购买力: ${buy_power:,.2f}, 剩余融资额度: ${remaining_finance:,.2f}"
        )

        # 🔧 融资账户检测与资金判断修复
        if available_cash < 0:
            # 现金为负数 = 融资账户（已使用融资）
            logger.info(
                f"  💳 {symbol}: 融资账户检测\n"
                f"     现金余额: ${available_cash:.2f} (负数表示融资债务)\n"
                f"     剩余融资额度: ${remaining_finance:,.2f}"
            )
            # ✅ 修复：使用剩余融资额度判断，而非购买力
            if remaining_finance > 1000:
                logger.info(f"  ✅ 融资额度充足，可以继续交易 (${remaining_finance:,.2f})")
            else:
                logger.warning(
                    f"  ⏭️ 融资额度不足，跳过交易\n"
                    f"     剩余额度: ${remaining_finance:,.2f} < $1,000"
                )
                raise InsufficientFundsError(
                    f"融资额度不足（剩余${remaining_finance:,.2f}，需要>$1,000）"
                )

        # 4. 计算动态预算
        dynamic_budget = await self._calculate_dynamic_budget(account, signal)

        # 5. 获取手数
        lot_size = await self.lot_size_helper.get_lot_size(symbol, self.quote_client)

        # 6. 计算购买数量
        quantity = self.lot_size_helper.calculate_order_quantity(
            symbol, dynamic_budget, current_price, lot_size
        )

        # 7. 计算所需资金和手数
        num_lots = quantity // lot_size if quantity > 0 else 0
        required_cash = current_price * quantity if quantity > 0 else lot_size * current_price

        # 8. 资金不足检查（统一处理，触发智能轮换）
        if quantity <= 0 or dynamic_budget < (lot_size * current_price):
            logger.warning(
                f"  ⚠️ {symbol}: 动态预算不足 "
                f"(需要至少1手: ${required_cash:.2f}, 可用: ${available_cash:.2f})"
            )
            logger.info(
                f"  📊 当前状态: 币种={currency}, 手数={lot_size}, "
                f"价格=${current_price:.2f}, 信号评分={score}"
            )
            logger.warning(
                f"  ⚠️ {symbol}: 资金不足 "
                f"(需要 ${required_cash:.2f}, 可用 ${available_cash:.2f})"
            )
            logger.info(
                f"  📊 当前状态: 币种={currency}, 数量={quantity}股, "
                f"价格=${current_price:.2f}, 信号评分={score}"
            )

            # 尝试智能持仓轮换释放资金
            needed_amount = required_cash - available_cash

            # 🔥 关键修复：只在确实需要资金且信号质量足够高时才触发轮换
            if needed_amount > 0 and score >= 60:
                logger.info(
                    f"  🔄 尝试智能持仓轮换释放 ${needed_amount:,.2f}...\n"
                    f"     策略: 卖出评分较低的持仓，为评分{score}分的新信号腾出空间"
                )

                rotation_success, freed_amount = await self._try_smart_rotation(
                    signal, needed_amount
                )
            elif needed_amount <= 0:
                # 资金已经足够，不应该到这里
                logger.warning(
                    f"  ⚠️ 预算计算异常: needed_amount=${needed_amount:.2f}（资金已充足但quantity=0）\n"
                    f"     说明: 动态预算${dynamic_budget:.2f}不足以购买1手（需${required_cash:.2f}），"
                    f"但可用资金${available_cash:.2f}充足"
                )
                raise InsufficientFundsError(
                    f"动态预算不足（预算${dynamic_budget:.2f} < 1手${required_cash:.2f}）"
                )
            else:
                # 低分信号（<60分）不触发轮换，避免为低质量信号卖出好持仓
                logger.warning(
                    f"  ⚠️ {symbol}: 信号评分{score}分 < 60分，不触发持仓轮换\n"
                    f"     说明: 低分信号不应卖出现有持仓，建议等待更高质量的交易机会"
                )
                rotation_success = False
                freed_amount = 0

            if rotation_success:
                logger.success(f"  ✅ 智能轮换成功，已释放 ${freed_amount:,.2f}")

                # 重新获取账户信息（轮换后强制刷新缓存）
                try:
                    account = await self._get_account_with_cache(force_refresh=True)
                    available_cash = float(account["cash"].get(currency, 0))

                    if available_cash >= required_cash:
                        logger.success(f"  💰 轮换后可用资金: ${available_cash:,.2f}，继续执行订单")

                        # 重新计算动态预算和购买数量
                        dynamic_budget = await self._calculate_dynamic_budget(account, signal)

                        quantity = self.lot_size_helper.calculate_order_quantity(
                            symbol, dynamic_budget, current_price, lot_size
                        )

                        if quantity <= 0:
                            raise InsufficientFundsError(
                                f"轮换后预算仍不足以购买1手（预算${dynamic_budget:.2f}）"
                            )

                        # 更新 num_lots 和 required_cash
                        num_lots = quantity // lot_size
                        required_cash = current_price * quantity

                        logger.info(
                            f"  📊 轮换后重新计算: 预算=${dynamic_budget:.2f}, "
                            f"数量={quantity}股 ({num_lots}手), 需要${required_cash:.2f}"
                        )
                    else:
                        logger.warning(
                            f"  ⚠️ 轮换后资金仍不足 "
                            f"(需要 ${required_cash:.2f}, 可用 ${available_cash:.2f})"
                        )
                        raise InsufficientFundsError(
                            f"轮换后资金仍不足（需要${required_cash:.2f}，可用${available_cash:.2f}）"
                        )
                except Exception as e:
                    logger.error(f"  ❌ 重新获取账户信息失败: {e}")
                    raise
            else:
                logger.warning(f"  ⚠️ 智能轮换未能释放足够资金")
                raise InsufficientFundsError(
                    f"资金不足且无法通过轮换释放（需要${required_cash:.2f}，可用${available_cash:.2f}）"
                )

        # 8. 获取买卖盘价格
        bid_price, ask_price = await self._get_bid_ask(symbol)

        # 9. 计算下单价格
        order_price = self._calculate_order_price(
            "BUY",
            current_price,
            bid_price=bid_price,
            ask_price=ask_price,
            atr=signal.get('indicators', {}).get('atr'),
            symbol=symbol
        )

        # 9.1 券商额度终检：防止明知可买量为0仍然走下单流程
        broker_max_qty_final = await self._estimate_available_quantity(
            symbol=symbol,
            price=order_price,
            lot_size=lot_size,
            currency=None
        )

        if broker_max_qty_final <= 0:
            fallback_qty = await self._fallback_cash_estimate(
                symbol=symbol,
                price=order_price,
                lot_size=lot_size
            )

            if fallback_qty <= 0:
                reason_lines = [
                    f"❌ 无法买入 {symbol}:",
                    f"   • 券商预估可买数量为0股 (< {lot_size}股)",
                    f"   • 订单参考价: ${order_price:.2f}",
                    f"   • 买入力: ${buy_power:.2f}, 剩余融资: ${remaining_finance:.2f}",
                    "   💡 建议: 归还部分融资或等待持仓结算释放购买力"
                ]
                raise InsufficientFundsError("\n".join(reason_lines))

            logger.info(
                f"  ✅ 使用Fallback估算替代券商可买量: {fallback_qty}股"
            )
            broker_max_qty_final = fallback_qty

        if quantity > broker_max_qty_final:
            logger.warning(
                f"  ⚠️ 请求数量{quantity}超过券商允许{broker_max_qty_final}，自动调整"
            )
            if broker_max_qty_final < lot_size:
                raise InsufficientFundsError(
                    f"券商可买数量仅{broker_max_qty_final}股，低于最小手数{lot_size}股"
                )

            quantity = (broker_max_qty_final // lot_size) * lot_size
            num_lots = quantity // lot_size
            required_cash = order_price * quantity

        if quantity <= 0:
            raise InsufficientFundsError(
                f"券商限额不足，无法买入 {symbol}（允许0股）"
            )

        # 10. 提交订单（分批建仓 或 TWAP策略）
        try:
            # 🔥 根据配置选择建仓策略
            if self.enable_staged_entry and score < 80:
                # 启用分批建仓（仅对非极强信号）
                logger.info(f"📊 使用分批建仓策略（信号评分{score}分）...")

                # 🔒 标记执行状态（防止重复信号）
                await self._mark_twap_execution(symbol, duration_seconds=3600)

                try:
                    final_quantity, final_price = await self._execute_staged_buy(
                        signal=signal,
                        total_budget=dynamic_budget,
                        current_price=order_price
                    )

                    if final_quantity == 0:
                        raise Exception("分批建仓未成交")
                finally:
                    # 🔓 执行完成后移除标记
                    await self._unmark_twap_execution(symbol)

            else:
                # 使用传统TWAP策略（一次性建仓，分批执行降低冲击）
                order_request = OrderRequest(
                    symbol=symbol,
                    side="BUY",
                    quantity=quantity,
                    order_type="LIMIT",
                    limit_price=order_price,
                    strategy=ExecutionStrategy.TWAP,  # 使用TWAP策略
                    urgency=5,  # 中等紧急度
                    max_slippage=0.01,  # 允许1%滑点
                    signal=signal,
                    metadata={
                        "signal_type": signal_type,
                        "score": score,
                        "stop_loss": signal.get('stop_loss'),
                        "take_profit": signal.get('take_profit')
                    }
                )

                # 🔒 标记TWAP执行状态（防止重复信号，持续1小时）
                await self._mark_twap_execution(symbol, duration_seconds=3600)

                # 执行TWAP订单
                logger.info(f"📊 使用TWAP策略执行订单（将在30分钟内分批下单）...")
                try:
                    execution_result = await self.smart_router.execute_order(order_request)

                    if not execution_result.success:
                        raise Exception(f"订单执行失败: {execution_result.error_message}")
                finally:
                    # 🔓 执行完成后移除标记（无论成功或失败）
                    await self._unmark_twap_execution(symbol)

                # 使用实际成交的数量和价格（不使用默认值）
                final_price = execution_result.average_price
                final_quantity = execution_result.filled_quantity

            # 🔥 检查是否有实际成交
            if final_quantity == 0:
                logger.error(
                    f"\n❌ TWAP订单未成交: {execution_result.order_id}\n"
                    f"   标的: {symbol}\n"
                    f"   类型: {signal_type}\n"
                    f"   评分: {score}/100\n"
                    f"   请求数量: {quantity}股\n"
                    f"   实际成交: 0股\n"
                    f"   原因: {execution_result.error_message or '未知'}"
                )
                # 不更新持仓，直接返回（在外层会抛出异常）
                raise Exception(f"订单未成交: {execution_result.error_message or '订单被拒绝'}")

            logger.success(
                f"\n✅ TWAP开仓订单已完成: {execution_result.order_id}\n"
                f"   标的: {symbol}\n"
                f"   类型: {signal_type}\n"
                f"   评分: {score}/100\n"
                f"   数量: {final_quantity}股 ({final_quantity//lot_size}手 × {lot_size}股/手)\n"
                f"   平均价: ${final_price:.2f}\n"
                f"   总额: ${final_price * final_quantity:.2f}\n"
                f"   滑点: {execution_result.slippage*100:.2f}%\n"
                f"   子订单: {len(execution_result.child_orders)}个\n"
                f"   止损位: ${signal.get('stop_loss', 0):.2f}\n"
                f"   止盈位: ${signal.get('take_profit', 0):.2f}"
            )

            # 用于后续逻辑的订单信息（保持兼容性）
            order = {
                'order_id': execution_result.order_id,
                'child_orders': execution_result.child_orders
            }

            # 🔥 【关键修复】立即更新Redis持仓（防止重复开仓）
            # 只有实际成交时才更新持仓
            try:
                await self.position_manager.add_position(
                    symbol=symbol,
                    quantity=final_quantity,  # 使用实际成交数量
                    cost_price=final_price,   # 使用TWAP平均价
                    order_id=order.get('order_id', ''),
                    notify=True  # 发布Pub/Sub通知
                )
                logger.info(f"  ✅ Redis持仓已更新: {symbol} (TWAP平均价: ${final_price:.2f})")
            except Exception as e:
                logger.error(f"  ❌ Redis持仓更新失败: {e}")
                # 不影响订单执行，继续

            # 🔥 【关键修复】保存订单记录到数据库（防止重复买入）
            # 保存所有子订单记录
            try:
                # 保存父订单（主订单）
                await self.order_manager.save_order(
                    order_id=order.get('order_id', ''),
                    symbol=symbol,
                    side="BUY",
                    quantity=final_quantity,  # 使用实际成交数量
                    price=final_price,        # 使用TWAP平均价
                    status="Filled" if execution_result.filled_quantity == quantity else "Partial"
                )
                logger.info(f"  ✅ 订单记录已保存: {order.get('order_id', '')} ({len(execution_result.child_orders)}个子订单)")
            except Exception as e:
                logger.error(f"  ❌ 订单记录保存失败: {e}")
                # 不影响订单执行，继续

            # 11. 记录止损止盈
            self.positions_with_stops[symbol] = {
                "entry_price": current_price,
                "stop_loss": signal.get('stop_loss'),
                "take_profit": signal.get('take_profit'),
                "atr": signal.get('indicators', {}).get('atr'),
            }

            # 🔥 智能评估是否提交备份条件单（LIT）- 混合止损策略
            backup_stop_order_id = None
            backup_profit_order_id = None

            if self.settings.backup_orders.enabled:
                # 执行风险评估
                risk_assessment = self.risk_assessor.assess(
                    symbol=symbol,
                    signal=signal,
                    quantity=final_quantity,
                    price=final_price
                )

                # 打印风险评估结果
                logger.info(self.risk_assessor.format_assessment_log(risk_assessment))

                # 根据评估结果决定是否提交备份条件单
                if risk_assessment['should_backup']:
                    # 🔥 低分信号保护：分数<60的信号不提交备份条件单（降低探索性仓位风险）
                    signal_score = signal.get('score', 0)
                    if signal_score < 60:
                        logger.info(
                            f"  ⏭️ 跳过备份条件单: 信号分数较低({signal_score}分 < 60分)，"
                            f"仅依赖客户端监控止损/止盈（降低误触风险）"
                        )
                    else:
                        try:
                            stop_loss = signal.get('stop_loss')
                            take_profit = signal.get('take_profit')

                            if stop_loss and stop_loss > 0:
                                # 🔥 智能选择：跟踪止损 vs 固定止损
                                if self.settings.backup_orders.use_trailing_stop:
                                    # 使用跟踪止损（TSLPPCT）- 自动跟随价格上涨锁定利润
                                    # 🔥 修复：side应该是"BUY"表示保护多头仓位，而非"SELL"
                                    stop_result = await self.trade_client.submit_trailing_stop(
                                        symbol=symbol,
                                        side="BUY",  # 修复：保护多头仓位（买入后持有）
                                        quantity=final_quantity,
                                        trailing_percent=self.settings.backup_orders.trailing_stop_percent,
                                        limit_offset=self.settings.backup_orders.trailing_stop_limit_offset,
                                        expire_days=self.settings.backup_orders.trailing_stop_expire_days,
                                        remark=f"Trailing Stop {self.settings.backup_orders.trailing_stop_percent*100:.1f}%"
                                    )
                                    backup_stop_order_id = stop_result.get('order_id')
                                    logger.success(
                                        f"  ✅ 跟踪止损备份单已提交: {backup_stop_order_id} "
                                        f"(跟踪{self.settings.backup_orders.trailing_stop_percent*100:.1f}%)"
                                    )
                                else:
                                    # 使用固定止损（LIT）- 传统到价止损
                                    stop_loss_float = float(stop_loss)
                                    stop_result = await self.trade_client.submit_conditional_order(
                                        symbol=symbol,
                                        side="SELL",
                                        quantity=final_quantity,
                                        trigger_price=stop_loss_float,
                                        limit_price=stop_loss_float * 0.995,  # 触发后以略低价格限价卖出，确保成交
                                        remark=f"Backup Stop Loss @ ${stop_loss_float:.2f}"
                                    )
                                    backup_stop_order_id = stop_result.get('order_id')
                                    logger.success(f"  ✅ 固定止损备份条件单已提交: {backup_stop_order_id}")

                            if take_profit and take_profit > 0:
                                # 🔥 智能选择：跟踪止盈 vs 固定止盈（实现"让利润奔跑"）
                                if self.settings.backup_orders.use_trailing_profit:
                                    # 使用跟踪止盈（TSMPCT）- 不限制上涨空间，仅在回撤时退出
                                    # 🔥 修复：side应该是"BUY"表示保护多头仓位，而非"SELL"
                                    profit_result = await self.trade_client.submit_trailing_profit(
                                        symbol=symbol,
                                        side="BUY",  # 修复：保护多头仓位（买入后持有）
                                        quantity=final_quantity,
                                        trailing_percent=self.settings.backup_orders.trailing_profit_percent,
                                        limit_offset=self.settings.backup_orders.trailing_profit_limit_offset,
                                        expire_days=self.settings.backup_orders.trailing_profit_expire_days,
                                        remark=f"Trailing Profit {self.settings.backup_orders.trailing_profit_percent*100:.1f}%"
                                    )
                                    backup_profit_order_id = profit_result.get('order_id')
                                    logger.success(
                                        f"  ✅ 跟踪止盈备份单已提交: {backup_profit_order_id} "
                                        f"(跟踪{self.settings.backup_orders.trailing_profit_percent*100:.1f}%)"
                                    )
                                else:
                                    # 使用固定止盈（LIT）- 传统到价止盈
                                    take_profit_float = float(take_profit)
                                    profit_result = await self.trade_client.submit_conditional_order(
                                        symbol=symbol,
                                        side="SELL",
                                        quantity=final_quantity,
                                        trigger_price=take_profit_float,
                                        limit_price=take_profit_float,  # 止盈使用触发价本身
                                        remark=f"Backup Take Profit @ ${take_profit_float:.2f}"
                                    )
                                    backup_profit_order_id = profit_result.get('order_id')
                                    logger.success(f"  ✅ 固定止盈备份条件单已提交: {backup_profit_order_id}")

                            # 打印策略说明
                            stop_type = "跟踪止损(TSLPPCT)" if self.settings.backup_orders.use_trailing_stop else "固定止损(LIT)"
                            profit_type = "跟踪止盈(TSMPCT)" if self.settings.backup_orders.use_trailing_profit else "固定止盈(LIT)"
                            logger.info(f"  📋 备份条件单策略: 客户端监控（主） + 交易所{stop_type}+{profit_type}（备份）")

                        except Exception as e:
                            logger.warning(f"⚠️ 提交备份条件单失败（不影响主流程）: {e}")
                            import traceback
                            logger.debug(f"  详细错误: {traceback.format_exc()}")
                            # 即使备份条件单失败，也继续保存止损设置（客户端监控仍然工作）
                else:
                    logger.info(f"  ℹ️ 低风险交易，依赖客户端监控（节省成本）")
            else:
                logger.info(f"  ⚙️ 备份条件单功能已禁用")

            # 保存到数据库（包括备份条件单ID）
            try:
                # 统一转换为 float 避免类型错误
                await self.stop_manager.save_stop(
                    symbol=symbol,
                    entry_price=float(final_price),  # 使用实际成交均价
                    stop_loss=float(signal.get('stop_loss')) if signal.get('stop_loss') else None,
                    take_profit=float(signal.get('take_profit')) if signal.get('take_profit') else None,
                    atr=float(signal.get('indicators', {}).get('atr')) if signal.get('indicators', {}).get('atr') else None,
                    quantity=int(final_quantity),  # 转换为 int
                    strategy='advanced_technical',
                    backup_stop_loss_order_id=backup_stop_order_id,
                    backup_take_profit_order_id=backup_profit_order_id
                )
            except Exception as e:
                logger.warning(f"⚠️ 保存止损止盈失败: {e}")
                import traceback
                logger.debug(f"  详细错误: {traceback.format_exc()}")

            # 12. 发送Slack通知
            if self.slack:
                await self._send_buy_notification(symbol, signal, order, quantity, order_price, required_cash)

        except Exception as e:
            logger.error(f"❌ 提交订单失败: {e}")

            # 静默错误列表（这些错误不发送Slack通知，避免噪音）
            silent_errors = [
                "可买数量为0",
                "Fallback也失败",
                "资金不足",
                "购买力不足",
                "融资额度不足",
                "动态预算不足"
            ]

            # 判断是否为静默错误
            error_msg = str(e)
            is_silent = any(silent_err in error_msg for silent_err in silent_errors)

            if is_silent:
                # 静默处理：只记录日志，不发送Slack通知
                logger.debug(
                    f"  ℹ️ 静默处理预期错误（不发送Slack通知）: {error_msg}\n"
                    f"     原因: 此类错误应在信号生成阶段预检查，到此说明是漏网之鱼"
                )
            else:
                # 发送失败通知到 Slack（仅对非预期错误）
                if self.slack:
                    await self._send_failure_notification(
                        symbol=symbol,
                        signal=signal,
                        error=error_msg
                    )

            raise

    async def _regime_updater(self):
        """周期性更新市场状态（牛/熊/震荡）。"""
        interval = max(3, int(getattr(self.settings, 'regime_update_interval_minutes', 10))) * 60
        while True:
            try:
                # 获取市场状态（根据交易时段自动过滤指数）
                res = await self.regime_classifier.classify(self.quote_client, filter_by_market=True)

                # 如果非交易时段或无指数配置，跳过通知
                if res.active_market == "NONE":
                    logger.debug(f"⏰ 非交易时段，跳过Regime检查")
                    await asyncio.sleep(interval)
                    continue

                # 如果当前市场无可用指数，跳过通知（例如：只配置了美股指数，但现在是港股时段）
                if "无指数配置" in res.details:
                    logger.debug(f"⏭️  当前{res.active_market}市场时段无可用指数配置，跳过Regime检查")
                    await asyncio.sleep(interval)
                    continue

                if res.regime != self.current_regime:
                    logger.info(f"📈 Regime变更: {self.current_regime} → {res.regime} | {res.details}")
                    # 发送通知
                    if self.slack:
                        try:
                            await self._send_regime_notification(res)
                        except Exception as e:
                            logger.debug(f"发送Regime通知失败: {e}")
                else:
                    logger.debug(f"Regime维持: {res.regime} | {res.details}")
                self.current_regime = res.regime

                # 每日汇总或变更时发送当日仓位/预留预算汇总
                try:
                    now_day = datetime.now(self.beijing_tz).strftime('%Y-%m-%d')
                    need_summary = (self._last_regime_summary_day != now_day) or (self._last_regime_notified != res.regime)
                    if self.slack and need_summary:
                        await self._send_regime_daily_summary(res)
                        self._last_regime_summary_day = now_day
                        self._last_regime_notified = res.regime
                except Exception as e:
                    logger.debug(f"发送Regime汇总失败: {e}")
            except Exception as e:
                logger.warning(f"⚠️ 更新Regime失败: {e}")
            await asyncio.sleep(interval)

    async def _intraday_style_updater(self):
        """周期性评估当日风格（趋势/震荡），快速微调仓位与预留。"""
        interval = max(1, int(getattr(self.settings, 'intraday_update_interval_minutes', 3))) * 60
        while True:
            try:
                style, details = await self.regime_classifier.classify_intraday_style(self.quote_client)

                # 如果当前市场无可用指数，跳过检查
                if "无指数配置" in details:
                    logger.debug(f"⏭️  当前市场时段无可用指数配置，跳过日内风格检查")
                    await asyncio.sleep(interval)
                    continue

                if style != self.current_intraday_style:
                    logger.info(f"📊 日内风格变更: {self.current_intraday_style} → {style} | {details}")
                    if self.slack:
                        try:
                            await self._send_intraday_style_notification(style, details)
                        except Exception as e:
                            logger.debug(f"发送日内风格通知失败: {e}")
                else:
                    logger.debug(f"日内风格维持: {style} | {details}")
                self.current_intraday_style = style
            except Exception as e:
                logger.warning(f"⚠️ 更新日内风格失败: {e}")
            await asyncio.sleep(interval)

    async def _rebalancer_updater(self):
        """周期性触发基于Regime的去杠杆，发布减仓信号。"""
        interval = max(5, int(getattr(self.settings, 'rebalancer_min_interval_minutes', 30))) * 60
        while True:
            try:
                regime, plan = await self.rebalancer.run_once()
                if plan:
                    total_qty = sum(p.sell_qty for p in plan)
                    total_value = sum(p.sell_qty * p.price for p in plan)
                    msg = (
                        f"🧯 *Regime去杠杆执行*\n\n"
                        f"状态: {regime}\n"
                        f"标的数: {len(plan)}\n"
                        f"数量合计: {total_qty} 股\n"
                        f"估算成交额: ${total_value:,.0f}\n"
                    )
                    logger.info(msg.replace('*',''))
                    if self.slack:
                        try:
                            await self.slack.send(msg)
                        except Exception as e:
                            logger.debug(f"发送去杠杆通知失败: {e}")
                else:
                    logger.debug("去杠杆检查：当前无需减仓")
            except Exception as e:
                logger.warning(f"⚠️ 去杠杆任务失败: {e}")
            await asyncio.sleep(interval)

    async def _queue_status_notifier(self):
        """周期性发送队列状态摘要（每小时）"""
        interval = 3600  # 1小时
        last_empty_alert_time = 0
        consecutive_empty_count = 0

        while True:
            try:
                await asyncio.sleep(interval)

                # 获取队列状态
                queue_size = await self.signal_queue.get_queue_size()
                delayed_count = await self.signal_queue.count_delayed_signals(
                    account=self.settings.account_id
                )

                # 🔥 获取延迟信号详情（用于监控）
                delayed_signals_info = ""
                if delayed_count > 0:
                    try:
                        delayed_signals = await self.signal_queue.get_delayed_signals(
                            account=self.settings.account_id
                        )

                        if delayed_signals:
                            now = time.time()
                            remaining_delays = []
                            total_ages = []

                            for sig in delayed_signals:
                                retry_after = sig.get('retry_after', 0)
                                remaining = max(0, retry_after - now) / 60
                                remaining_delays.append(remaining)

                                # 计算信号总存在时间
                                queued_at_str = sig.get('queued_at')
                                if queued_at_str:
                                    try:
                                        queued_at = datetime.fromisoformat(queued_at_str)
                                        total_age = (datetime.now() - queued_at).total_seconds() / 60
                                        total_ages.append(total_age)
                                    except:
                                        pass

                            if remaining_delays:
                                avg_remaining = sum(remaining_delays) / len(remaining_delays)
                                max_remaining = max(remaining_delays)
                                avg_age = sum(total_ages) / len(total_ages) if total_ages else 0
                                max_age = max(total_ages) if total_ages else 0

                                delayed_signals_info = (
                                    f"   • 剩余延迟时间：平均{avg_remaining:.1f}分钟，最长{max_remaining:.1f}分钟\n"
                                    f"   • 信号存在时间：平均{avg_age:.1f}分钟，最长{max_age:.1f}分钟"
                                )

                                # 🔥 如果有信号存在时间过长（>30分钟），记录警告
                                if max_age > 30:
                                    logger.warning(
                                        f"⚠️ 发现长时间延迟信号：已存在{max_age:.1f}分钟，"
                                        f"还需等待{max_remaining:.1f}分钟"
                                    )
                    except Exception as e:
                        logger.debug(f"  获取延迟信号详情失败: {e}")

                # 获取账户信息
                try:
                    account = await self.trade_client.get_account()
                    hkd_cash = float(account["cash"].get("HKD", 0))
                    usd_cash = float(account["cash"].get("USD", 0))
                    hkd_power = float(account.get("buy_power", {}).get("HKD", 0))
                    usd_power = float(account.get("buy_power", {}).get("USD", 0))
                except:
                    hkd_cash = usd_cash = hkd_power = usd_power = 0.0

                # 队列长时间为空的警告（连续3小时）
                if queue_size == 0:
                    consecutive_empty_count += 1
                    if consecutive_empty_count >= 3 and (time.time() - last_empty_alert_time) > 10800:
                        # 检查 VIXY 恐慌状态
                        vixy_status = await self._get_vixy_status_from_redis()

                        # 根据 VIXY 状态生成不同的警告消息
                        if vixy_status and vixy_status.get('panic'):
                            # VIXY 恐慌模式导致的队列为空
                            vixy_price = vixy_status.get('price', 0)
                            vixy_threshold = vixy_status.get('threshold', 30.0)
                            vixy_ma200 = vixy_status.get('ma200', '')

                            message = (
                                f"🚨 **队列长时间为空警告**\n\n"
                                f"📊 队列已连续 {consecutive_empty_count} 小时为空\n\n"
                                f"**主要原因：VIXY 恐慌模式已触发**\n\n"
                                f"📉 **VIXY 恐慌指数状态：**\n"
                                f"   • 当前价格: **${vixy_price:.2f}**\n"
                                f"   • 恐慌阈值: ${vixy_threshold:.2f}\n"
                            )
                            if vixy_ma200:
                                message += f"   • MA200: ${vixy_ma200}\n"
                            message += (
                                f"\n⚠️  **已自动停止生成买入信号**\n"
                                f"当 VIXY 降至 ${vixy_threshold:.2f} 以下时将自动恢复\n\n"
                                f"💡 如需调整阈值，请修改环境变量 `VIXY_PANIC_THRESHOLD`"
                            )
                        else:
                            # 其他原因导致的队列为空
                            message = (
                                f"⚠️ **队列长时间为空警告**\n\n"
                                f"📊 队列已连续 {consecutive_empty_count} 小时为空\n\n"
                                f"可能原因：\n"
                                f"   • 信号生成器未运行\n"
                                f"   • 市场无交易机会\n"
                                f"   • 所有策略已关闭\n\n"
                                f"💡 建议检查信号生成器和策略配置"
                            )

                        if self.slack:
                            await self.slack.send(message)
                        last_empty_alert_time = time.time()
                else:
                    consecutive_empty_count = 0

                # 正常的每小时摘要（只在队列有信号或有延迟信号时发送）
                if queue_size > 0 or delayed_count > 0:
                    status_emoji = "✅" if delayed_count == 0 else "⚠️"

                    message = (
                        f"{status_emoji} **队列状态摘要**\n\n"
                        f"📊 **队列统计：**\n"
                        f"   • 待处理信号: {queue_size}个\n"
                        f"   • 延迟信号: {delayed_count}个\n\n"
                        f"💰 **账户状态：**\n"
                        f"   • HKD现金: ${hkd_cash:,.2f}\n"
                        f"   • HKD购买力: ${hkd_power:,.2f}\n"
                        f"   • USD现金: ${usd_cash:,.2f}\n"
                        f"   • USD购买力: ${usd_power:,.2f}\n\n"
                        f"🕐 下次汇报: 1小时后"
                    )

                    if delayed_count > 0:
                        message += f"\n\n💡 **提示:** 有{delayed_count}个信号因资金不足延迟处理"
                        if delayed_signals_info:
                            message += f"\n\n📊 **延迟信号详情：**\n{delayed_signals_info}"

                    if self.slack:
                        await self.slack.send(message)

                logger.debug(f"队列状态摘要已发送: {queue_size}个待处理, {delayed_count}个延迟")

            except Exception as e:
                logger.warning(f"⚠️ 发送队列状态摘要失败: {e}")

    async def _delayed_signal_cleaner(self):
        """周期性清理超时的延迟信号（每10分钟）"""
        interval = 600  # 10分钟

        while True:
            try:
                await asyncio.sleep(interval)

                # 获取所有延迟信号
                delayed_signals = await self.signal_queue.get_delayed_signals(
                    account=self.settings.account_id
                )

                if not delayed_signals:
                    continue

                # 检查每个延迟信号是否超时
                now = time.time()
                max_total_age = self.settings.signal_ttl_seconds  # 使用信号TTL作为最大存在时间
                cleaned_count = 0

                for signal in delayed_signals:
                    try:
                        # 检查信号总存在时间
                        queued_at_str = signal.get('queued_at')
                        if not queued_at_str:
                            continue

                        queued_at = datetime.fromisoformat(queued_at_str)
                        total_age = (datetime.now() - queued_at).total_seconds()

                        # 如果信号存在时间超过TTL，强制删除
                        if total_age > max_total_age:
                            symbol = signal.get('symbol')
                            retry_after = signal.get('retry_after', 0)
                            remaining_delay = max(0, retry_after - now) / 60

                            logger.warning(
                                f"🗑️ 清理超时延迟信号: {symbol}, "
                                f"已存在{total_age/60:.1f}分钟 (> {max_total_age/60:.1f}分钟), "
                                f"retry_after还剩{remaining_delay:.1f}分钟"
                            )

                            # 标记为失败并删除
                            await self.signal_queue.mark_failed(
                                signal,
                                error_message=f"延迟信号超时（存在{total_age/60:.1f}分钟）"
                            )
                            cleaned_count += 1

                    except Exception as e:
                        logger.warning(f"⚠️ 检查延迟信号失败: {e}")
                        continue

                if cleaned_count > 0:
                    logger.info(f"✅ 已清理{cleaned_count}个超时延迟信号")

            except Exception as e:
                logger.warning(f"⚠️ 延迟信号清理任务失败: {e}")

    async def _get_vixy_status_from_redis(self) -> Optional[Dict]:
        """
        从 Redis 读取 VIXY 恐慌指数状态

        Returns:
            Dict: VIXY状态字典，包含：
                - price: float - 当前价格
                - panic: bool - 是否处于恐慌模式
                - threshold: float - 恐慌阈值
                - ma200: str - MA200值
                - updated_at: str - 更新时间
            如果读取失败返回 None
        """
        try:
            import redis.asyncio as aioredis

            redis_client = aioredis.from_url(self.settings.redis_url)

            # 批量读取 VIXY 状态
            pipe = redis_client.pipeline()
            pipe.get("market:vixy:price")
            pipe.get("market:vixy:panic")
            pipe.get("market:vixy:threshold")
            pipe.get("market:vixy:ma200")
            pipe.get("market:vixy:updated_at")

            results = await pipe.execute()
            await redis_client.aclose()

            # 解析结果
            price_str, panic_str, threshold_str, ma200_str, updated_at_str = results

            if not price_str:
                # VIXY 状态不存在（可能信号生成器未运行）
                return None

            return {
                'price': float(price_str.decode('utf-8') if isinstance(price_str, bytes) else price_str),
                'panic': (panic_str.decode('utf-8') if isinstance(panic_str, bytes) else panic_str) == "1",
                'threshold': float(threshold_str.decode('utf-8') if isinstance(threshold_str, bytes) else threshold_str) if threshold_str else 30.0,
                'ma200': (ma200_str.decode('utf-8') if isinstance(ma200_str, bytes) else ma200_str) if ma200_str else '',
                'updated_at': (updated_at_str.decode('utf-8') if isinstance(updated_at_str, bytes) else updated_at_str) if updated_at_str else ''
            }

        except Exception as e:
            logger.debug(f"从 Redis 读取 VIXY 状态失败: {e}")
            return None

    async def _send_regime_notification(self, res):
        emoji = {'BULL': '🟢', 'RANGE': '🟡', 'BEAR': '🔴'}.get(res.regime, '🔘')
        reserve_map = {
            "BULL": float(getattr(self.settings, 'regime_reserve_pct_bull', 0.15) or 0.15),
            "RANGE": float(getattr(self.settings, 'regime_reserve_pct_range', 0.30) or 0.30),
            "BEAR": float(getattr(self.settings, 'regime_reserve_pct_bear', 0.50) or 0.50),
        }
        scale_map = {
            "BULL": float(getattr(self.settings, 'regime_position_scale_bull', 1.0) or 1.0),
            "RANGE": float(getattr(self.settings, 'regime_position_scale_range', 0.70) or 0.70),
            "BEAR": float(getattr(self.settings, 'regime_position_scale_bear', 0.40) or 0.40),
        }
        reserve = reserve_map.get(res.regime, 0.30)
        scale = scale_map.get(res.regime, 0.70)
        message = (
            f"{emoji} *市场状态变更*\n\n"
            f"状态: {res.regime}\n"
            f"依据: {res.details}\n\n"
            f"📋 策略参数:\n"
            f"  • 预留购买力: {reserve*100:.0f}%\n"
            f"  • 仓位缩放: ×{scale:.2f}\n"
        )
        await self.slack.send(message)

    async def _send_regime_daily_summary(self, res):
        try:
            account = await self.trade_client.get_account()
        except Exception as e:
            logger.debug(f"获取账户失败，无法发送汇总: {e}")
            return

        reserve_map = {
            "BULL": float(getattr(self.settings, 'regime_reserve_pct_bull', 0.15) or 0.15),
            "RANGE": float(getattr(self.settings, 'regime_reserve_pct_range', 0.30) or 0.30),
            "BEAR": float(getattr(self.settings, 'regime_reserve_pct_bear', 0.50) or 0.50),
        }
        scale_map = {
            "BULL": float(getattr(self.settings, 'regime_position_scale_bull', 1.0) or 1.0),
            "RANGE": float(getattr(self.settings, 'regime_position_scale_range', 0.70) or 0.70),
            "BEAR": float(getattr(self.settings, 'regime_position_scale_bear', 0.40) or 0.40),
        }
        reserve = reserve_map.get(res.regime, 0.30)
        scale = scale_map.get(res.regime, 0.70)

        lines = []
        for ccy in sorted(set(list(account.get('cash', {}).keys()) + list(account.get('buy_power', {}).keys()))):
            cash = float(account.get('cash', {}).get(ccy, 0) or 0)
            bp = float(account.get('buy_power', {}).get(ccy, 0) or 0)
            rem_fin = float(account.get('remaining_finance', {}).get(ccy, 0) or 0)
            cap = max(bp, max(0.0, cash) + max(0.0, rem_fin))
            cap_after = cap * (1 - reserve)
            lines.append(
                f"{ccy}: 上限${cap:,.0f} → 预留后${cap_after:,.0f} (预留{reserve*100:.0f}%)"
            )

        message = (
            "📊 *今日仓位/购买力预算*\n\n"
            f"状态: {res.regime} | {res.details}\n"
            f"仓位缩放: ×{scale:.2f}\n"
            "可动用资金上限(预估):\n"
            + "\n".join([f"  • {ln}" for ln in lines])
        )
        await self.slack.send(message)

    async def _execute_sell_order(self, signal: Dict):
        """执行卖出订单（止损/止盈）"""
        symbol = signal['symbol']
        signal_type = signal.get('type', 'SELL')
        quantity = signal.get('quantity', 0)
        current_price = signal.get('price', 0)
        reason = signal.get('reason', '平仓')

        # 🔥 取消备份条件单（客户端监控优先触发）
        try:
            stops = await self.stop_manager.get_stop_for_symbol(symbol)
            if stops:
                backup_stop_order_id = stops.get('backup_stop_loss_order_id')
                backup_profit_order_id = stops.get('backup_take_profit_order_id')

                cancelled_orders = []
                if backup_stop_order_id:
                    try:
                        await self.trade_client.cancel_order(backup_stop_order_id)
                        cancelled_orders.append(f"止损单({backup_stop_order_id})")
                    except Exception as e:
                        logger.debug(f"  取消止损备份单失败（可能已触发或不存在）: {e}")

                if backup_profit_order_id:
                    try:
                        await self.trade_client.cancel_order(backup_profit_order_id)
                        cancelled_orders.append(f"止盈单({backup_profit_order_id})")
                    except Exception as e:
                        logger.debug(f"  取消止盈备份单失败（可能已触发或不存在）: {e}")

                if cancelled_orders:
                    logger.info(f"  ✅ 已取消备份条件单: {', '.join(cancelled_orders)}")
                    logger.info(f"  📋 客户端监控触发在先，交易所备份单已作废")

        except Exception as e:
            logger.warning(f"⚠️ 查询/取消备份条件单失败（不影响主流程）: {e}")

        # 获取买卖盘
        bid_price, ask_price = await self._get_bid_ask(symbol)

        # 计算下单价格
        order_price = self._calculate_order_price(
            "SELL",
            current_price,
            bid_price=bid_price,
            ask_price=ask_price,
            symbol=symbol
        )

        # 🔍 价格陈旧性和跳空风险检查
        signal_price = signal.get('price', current_price)
        if signal_price and signal_price > 0:
            # 🔧 检查买卖盘数据是否可用（盘后可能为 None）
            if bid_price is None:
                logger.warning(
                    f"  ⚠️ {symbol}: 无法获取买卖盘价格（市场可能关闭），"
                    f"跳过价格偏差检查，使用下单价 ${order_price:.2f} 继续执行"
                )
                # 跳过价格偏差检查，继续执行订单
            else:
                price_deviation_pct = abs(bid_price - signal_price) / signal_price
                max_allowed_gap = 0.03  # 3% 最大允许偏差

                if price_deviation_pct > max_allowed_gap:
                    logger.error(
                        f"  ⚠️ {symbol}: 价格偏差过大，暂停下单\n"
                        f"     信号价格: ${signal_price:.2f}\n"
                        f"     当前买价: ${bid_price:.2f}\n"
                        f"     偏差: {price_deviation_pct*100:.2f}% > {max_allowed_gap*100:.0f}%\n"
                        f"     风险: 可能存在跳空或价格陈旧\n"
                        f"     处理: 跳过本次订单，等待下一个交易周期"
                    )

                    # 发送Slack警报（如果配置）
                    if self.slack:
                        try:
                            await self.slack.send(
                                f"⚠️ *卖单价格偏差警报*\n\n"
                                f"标的: `{symbol}`\n"
                                f"信号价格: ${signal_price:.2f}\n"
                                f"当前买价: ${bid_price:.2f}\n"
                                f"偏差: *{price_deviation_pct*100:.2f}%*\n"
                                f"原因: {reason}\n\n"
                                f"已暂停下单，等待价格稳定"
                            )
                        except Exception as e:
                            logger.debug(f"发送Slack警报失败: {e}")

                    return  # 跳过订单
                elif price_deviation_pct > 0.01:  # 1% 偏差警告
                    logger.warning(
                        f"  ⚠️ {symbol}: 价格有偏差（{price_deviation_pct*100:.2f}%），"
                        f"信号${signal_price:.2f} → 当前${bid_price:.2f}"
                    )

        # 提交订单（使用SmartOrderRouter的自适应策略）
        try:
            # 检查市场时段 - 避免非交易时段使用市价单
            from longport_quant.utils.market_hours import MarketHours
            current_market = MarketHours.get_current_market()
            is_market_closed = (current_market == "NONE")

            # 根据订单类型和市场状态设置策略和紧急度
            is_rebalancer_sell = "Regime去杠杆" in reason or "去杠杆" in reason

            if is_market_closed:
                # 市场关闭：强制使用低紧急度和PASSIVE策略（限价单）
                urgency_level = 3
                execution_strategy = ExecutionStrategy.PASSIVE
                logger.warning(
                    f"  ⏸️ {symbol}: 市场休市，强制使用PASSIVE策略（限价单）\n"
                    f"     原因: 避免开盘时市价单跳空风险\n"
                    f"     策略: urgency={urgency_level}, strategy=PASSIVE"
                )
            elif is_rebalancer_sell:
                # 去杠杆：低紧急度，强制限价单
                urgency_level = 3
                execution_strategy = ExecutionStrategy.PASSIVE
                logger.info(f"  📊 去杠杆卖单：使用限价单策略(urgency={urgency_level})，确保价格可控")
            else:
                # 止损/止盈：中等紧急度，使用限价单而非市价单
                urgency_level = 5
                execution_strategy = ExecutionStrategy.PASSIVE
                logger.info(f"  🛡️ 止损/止盈卖单：使用限价单策略(urgency={urgency_level})，避免滑点风险")

            # 创建订单请求
            order_request = OrderRequest(
                symbol=symbol,
                side="SELL",
                quantity=quantity,
                order_type="LIMIT",
                limit_price=order_price,
                strategy=execution_strategy,  # 根据市场状态选择策略
                urgency=urgency_level,  # 根据订单类型和市场状态动态调整紧急度
                max_slippage=0.015,  # 允许1.5%滑点
                signal=signal,
                metadata={
                    "reason": reason,
                    "signal_type": signal_type,
                    "market_state": current_market,
                    "forced_passive": is_market_closed
                }
            )

            # 执行订单
            logger.info(f"📊 使用自适应策略执行平仓订单（{reason}）...")
            execution_result = await self.smart_router.execute_order(order_request)

            if not execution_result.success:
                raise Exception(f"订单执行失败: {execution_result.error_message}")

            # 使用平均价格和填充数量
            final_price = execution_result.average_price if execution_result.average_price > 0 else order_price
            final_quantity = execution_result.filled_quantity if execution_result.filled_quantity > 0 else quantity

            logger.success(
                f"\n✅ 平仓订单已完成: {execution_result.order_id}\n"
                f"   标的: {symbol}\n"
                f"   原因: {reason}\n"
                f"   数量: {final_quantity}股\n"
                f"   平均价: ${final_price:.2f}\n"
                f"   总额: ${final_price * final_quantity:.2f}\n"
                f"   滑点: {execution_result.slippage*100:.2f}%"
            )

            # 用于后续逻辑的订单信息（保持兼容性）
            order = {
                'order_id': execution_result.order_id,
                'child_orders': execution_result.child_orders
            }

            # 🔥 【关键修复】立即从Redis移除持仓（允许再次买入）
            try:
                await self.position_manager.remove_position(
                    symbol=symbol,
                    notify=True  # 发布Pub/Sub通知
                )
                logger.info(f"  ✅ Redis持仓已移除: {symbol}")
            except Exception as e:
                logger.error(f"  ❌ Redis持仓移除失败: {e}")
                # 不影响订单执行，继续

            # 🔥 【关键修复】保存订单记录到数据库（防止重复卖出）
            try:
                await self.order_manager.save_order(
                    order_id=order.get('order_id', ''),
                    symbol=symbol,
                    side="SELL",
                    quantity=final_quantity,  # 使用实际成交数量
                    price=final_price,        # 使用实际平均价
                    status="Filled" if execution_result.filled_quantity == quantity else "Partial"
                )
                logger.info(f"  ✅ 订单记录已保存: {order.get('order_id', '')}")
            except Exception as e:
                logger.error(f"  ❌ 订单记录保存失败: {e}")
                # 不影响订单执行，继续

            # 清除止损止盈记录
            if symbol in self.positions_with_stops:
                del self.positions_with_stops[symbol]

            # 发送Slack通知
            if self.slack:
                await self._send_sell_notification(symbol, signal, order, final_quantity, final_price)

            # 🔥 卖出后检查并唤醒延迟信号（资金释放后可能可以处理）
            await self._check_delayed_signals()

        except Exception as e:
            logger.error(f"❌ 提交平仓订单失败: {e}")
            raise

    async def _calculate_dynamic_budget(self, account: Dict, signal: Dict) -> float:
        """
        计算动态预算（基于信号强度和风险）

        较高评分的信号分配更多资金
        """
        if not self.use_adaptive_budget:
            # 如果不使用动态预算，返回固定金额
            return 10000.0

        score = signal.get('score', 0)
        symbol = signal.get('symbol', '')
        currency = "HKD" if ".HK" in symbol else "USD"

        # 获取总资产
        net_assets = float(account.get("net_assets", {}).get(currency, 0))
        if net_assets <= 0:
            net_assets = 50000.0  # 默认值

        # 基础预算（总资产的百分比）
        base_budget = net_assets * self.min_position_size_pct

        # 根据评分调整预算（优化后：降低最大仓位25%）
        if score >= 80:
            # 极强买入信号：重仓（20-25%，从30-40%降低）
            budget_pct = 0.20 + (score - 80) / 400  # 80分=20%, 100分=25%
        elif score >= 60:
            # 强买入信号：标准仓（15-22%，从20-30%降低）
            budget_pct = 0.15 + (score - 60) * 0.07 / 20  # 60分=15%, 80分=22%
        elif score >= 45:
            # 买入信号：试探性小仓位（5-10%，微调上限）
            budget_pct = 0.05 + (score - 45) * 0.05 / 14  # 45分=5%, 59分=10%
        else:
            # 低于45分：不应该生成信号（WEAK_BUY已禁用）
            budget_pct = 0.05  # 兜底最小值

        # 限制在合理范围内
        budget_pct = max(self.min_position_size_pct, min(budget_pct, self.max_position_size_pct))

        dynamic_budget = net_assets * budget_pct

        # 🔥 不能超过该币种的实际购买力和融资额度
        available_cash = float(account.get("cash", {}).get(currency, 0))
        remaining_finance = float(account.get("remaining_finance", {}).get(currency, 0))
        buy_power = float(account.get("buy_power", {}).get(currency, 0))

        # 计算可支配上限：优先使用购买力，其次可用资金，最后剩余融资额度
        if buy_power and buy_power > 0:
            effective_cap = buy_power
            cap_source = f"{currency}购买力"
            # 购买力通常已考虑融资额度，但仍确保不超过可用资金+融资额度
            if remaining_finance > 0:
                max_finance_cap = max(0.0, available_cash + remaining_finance)
                if effective_cap > max_finance_cap > 0:
                    effective_cap = max_finance_cap
                    cap_source = f"{currency}可用资金+融资额度"
        else:
            effective_cap = max(available_cash, 0.0)
            cap_source = f"{currency}可用资金"
            if effective_cap <= 0 and remaining_finance > 0:
                effective_cap = remaining_finance
                cap_source = f"{currency}剩余融资额度"

        if effective_cap <= 0:
            logger.error(
                f"  ❌ {currency} 账户可支配资金不足（可用={available_cash:,.2f}, "
                f"购买力={buy_power:,.2f}, 融资额度={remaining_finance:,.2f}）"
            )
            raise InsufficientFundsError(f"{currency}可支配资金不足")

        # 根据Regime预留购买力（在cap层面扣除）和仓位缩放（在budget层面缩放）
        try:
            regime = self.current_regime or "RANGE"
            reserve_map = {
                "BULL": float(getattr(self.settings, 'regime_reserve_pct_bull', 0.15) or 0.15),
                "RANGE": float(getattr(self.settings, 'regime_reserve_pct_range', 0.30) or 0.30),
                "BEAR": float(getattr(self.settings, 'regime_reserve_pct_bear', 0.50) or 0.50),
            }
            scale_map = {
                "BULL": float(getattr(self.settings, 'regime_position_scale_bull', 1.0) or 1.0),
                "RANGE": float(getattr(self.settings, 'regime_position_scale_range', 0.70) or 0.70),
                "BEAR": float(getattr(self.settings, 'regime_position_scale_bear', 0.40) or 0.40),
            }
            reserve = min(max(reserve_map.get(regime, 0.30), 0.0), 0.9)
            scale = min(max(scale_map.get(regime, 0.70), 0.1), 1.5)

            # 注入日内风格微调
            try:
                style = self.current_intraday_style or "RANGE"
                style_scale_map = {
                    "TREND": float(getattr(self.settings, 'intraday_scale_trend', 1.10) or 1.10),
                    "RANGE": float(getattr(self.settings, 'intraday_scale_range', 0.85) or 0.85),
                }
                style_reserve_delta_map = {
                    "TREND": float(getattr(self.settings, 'intraday_reserve_delta_trend', -0.05) or -0.05),
                    "RANGE": float(getattr(self.settings, 'intraday_reserve_delta_range', 0.05) or 0.05),
                }
                style_scale = style_scale_map.get(style, 1.0)
                style_reserve_delta = style_reserve_delta_map.get(style, 0.0)
                # 先调整reserve，再调整scale
                reserve = min(max(reserve + style_reserve_delta, 0.0), 0.9)
                scale = min(max(scale * style_scale, 0.1), 1.5)
                logger.debug(
                    f"  ⛳ 日内微调: style={style}, reserveΔ={style_reserve_delta:+.2f}, scale×={style_scale:.2f}"
                )
            except Exception as e:
                logger.debug(f"日内微调失败（忽略）: {e}")

            # 先在cap层面保留现金
            effective_cap_after_reserve = max(0.0, effective_cap * (1.0 - reserve))
            if effective_cap_after_reserve < effective_cap:
                logger.debug(
                    f"  🧯 Regime预留购买力: {regime} 预留{reserve*100:.0f}% → 上限${effective_cap:,.2f}→${effective_cap_after_reserve:,.2f}"
                )
            effective_cap = effective_cap_after_reserve

            # 再对预算做仓位缩放
            dynamic_budget_pre = dynamic_budget
            dynamic_budget = dynamic_budget * scale
            if abs(dynamic_budget - dynamic_budget_pre) / (dynamic_budget_pre or 1) > 0.01:
                logger.debug(
                    f"  🎚️ Regime仓位缩放: {regime} ×{scale:.2f} → 预算${dynamic_budget_pre:,.2f}→${dynamic_budget:,.2f}"
                )
        except Exception as e:
            logger.debug(f"Regime预算调整失败（忽略）: {e}")

        # 🎲 集成 Kelly 公式：基于历史胜率和盈亏比动态调整仓位
        try:
            market = "HK" if ".HK" in symbol else ("US" if ".US" in symbol else None)
            kelly_position, kelly_info = await self.kelly_calculator.get_recommended_position(
                total_capital=net_assets,
                signal_score=score,
                symbol=symbol,
                market=market,
                regime=regime
            )

            # 取评分预算和 Kelly 推荐的较小值（双重保险）
            if kelly_position > 0 and kelly_position < dynamic_budget:
                logger.info(
                    f"  🎲 Kelly 保护: 评分预算=${dynamic_budget:,.2f}, "
                    f"Kelly推荐=${kelly_position:,.2f} (胜率={kelly_info.get('win_rate', 0):.1%}, "
                    f"盈亏比={kelly_info.get('profit_loss_ratio', 0):.2f}), "
                    f"采用较小值"
                )
                dynamic_budget = kelly_position
            elif kelly_position > 0:
                logger.debug(
                    f"  ℹ️ Kelly推荐=${kelly_position:,.2f} ≥ 评分预算=${dynamic_budget:,.2f}, "
                    f"保持评分预算"
                )
        except Exception as e:
            logger.debug(f"Kelly公式计算失败（忽略）: {e}")

        if dynamic_budget > effective_cap:
            logger.warning(
                f"  ⚠️ 动态预算${dynamic_budget:,.2f}超出{cap_source}${effective_cap:,.2f}，"
                f"调整为${effective_cap:,.2f}"
            )
            dynamic_budget = effective_cap

        logger.debug(
            f"  动态预算计算: 评分={score}, 预算比例={budget_pct:.2%}, "
            f"金额=${dynamic_budget:.2f}"
        )

        return dynamic_budget

    async def _send_intraday_style_notification(self, style: str, details: str):
        emoji = {'TREND': '📈', 'RANGE': '〰️'}.get(style, '📊')
        # 读取调整参数
        style_scale = (
            float(getattr(self.settings, 'intraday_scale_trend', 1.10)) if style == 'TREND'
            else float(getattr(self.settings, 'intraday_scale_range', 0.85))
        )
        style_reserve_delta = (
            float(getattr(self.settings, 'intraday_reserve_delta_trend', -0.05)) if style == 'TREND'
            else float(getattr(self.settings, 'intraday_reserve_delta_range', 0.05))
        )
        message = (
            f"{emoji} *日内风格更新*\n\n"
            f"风格: {style}\n"
            f"依据: {details}\n\n"
            f"📋 微调参数:\n"
            f"  • 预留购买力Δ: {style_reserve_delta*100:+.0f}%\n"
            f"  • 仓位缩放×: {style_scale:.2f}\n"
        )
        await self.slack.send(message)

    async def _estimate_available_quantity(
        self,
        symbol: str,
        price: float,
        lot_size: int,
        currency: Optional[str] = None
    ) -> int:
        """
        调用交易端口预估最大可买数量（含融资），并按手数取整。

        Returns:
            int: 按手数取整后的最大可买数量，若不可用返回0
        """
        try:
            estimate = await self.trade_client.estimate_max_purchase_quantity(
                symbol=symbol,
                order_type=openapi.OrderType.LO,
                side=openapi.OrderSide.Buy,
                price=price,
                currency=currency
            )

            candidates = []
            if getattr(estimate, "margin_max_qty", None):
                candidates.append(float(estimate.margin_max_qty))
            if getattr(estimate, "cash_max_qty", None):
                candidates.append(float(estimate.cash_max_qty))

            if not candidates:
                return 0

            max_qty = max(candidates)
            if max_qty <= 0:
                return 0

            lots = int(max_qty // lot_size)
            if lots <= 0:
                return 0

            return lots * lot_size

        except Exception as e:
            logger.debug(f"  ⚠️ 预估最大可买数量失败: {e}")
            return 0

    async def _fallback_cash_estimate(
        self,
        symbol: str,
        price: float,
        lot_size: int
    ) -> int:
        """
        Fallback现金估算：当broker estimate返回0时的备用方案

        使用50%现金进行保守估算，保留50%安全边际

        Returns:
            int: 按手数取整后的估算数量，若现金不足返回0
        """
        try:
            # 获取币种现金
            currency = "HKD" if symbol.endswith(".HK") else "USD"
            balance = await self.trade_client.account_balance()

            cash_dict = balance.get("cash", {})
            cash_available = float(cash_dict.get(currency, 0))

            # 如果没有现金，返回0
            if cash_available <= 0:
                logger.debug(f"  ⚠️ {currency}现金不足: ${cash_available:,.0f}")
                return 0

            # 使用50%现金进行保守估算
            conservative_cash = cash_available * 0.5
            estimated_qty = int(conservative_cash / price)

            # 按手数取整
            lots = int(estimated_qty // lot_size)
            if lots <= 0:
                return 0

            final_qty = lots * lot_size

            logger.warning(
                f"⚠️ Fallback现金估算 - {symbol}:\n"
                f"   {currency}现金: ${cash_available:,.0f} ✅\n"
                f"   保守策略: 使用50%现金 = ${conservative_cash:,.0f}\n"
                f"   估算数量: {final_qty}股 ({lots}手 × {lot_size}股/手)\n"
                f"   说明: Broker estimate返回0，但现金充足，尝试保守下单"
            )

            return final_qty

        except Exception as e:
            logger.error(f"  ❌ Fallback现金估算失败: {e}")
            return 0

    async def _get_bid_ask(self, symbol: str):
        """获取买卖盘价格"""
        try:
            depth = await self.quote_client.get_depth(symbol)
            bid_price = float(depth.bids[0].price) if depth.bids and len(depth.bids) > 0 else None
            ask_price = float(depth.asks[0].price) if depth.asks and len(depth.asks) > 0 else None

            if bid_price or ask_price:
                logger.debug(
                    f"  📊 买卖盘: 买一=${bid_price:.2f if bid_price else 0}, "
                    f"卖一=${ask_price:.2f if ask_price else 0}"
                )

            return bid_price, ask_price

        except Exception as e:
            logger.debug(f"  ⚠️ 获取买卖盘失败: {e}")
            return None, None

    def _calculate_order_price(
        self,
        side: str,
        current_price: float,
        bid_price: Optional[float] = None,
        ask_price: Optional[float] = None,
        atr: Optional[float] = None,
        symbol: str = ""
    ) -> float:
        """
        计算智能下单价格

        买入: 尝试在买一和卖一之间，但不超过当前价+0.5%
        卖出: 尝试在买一和卖一之间，但不低于当前价-0.5%
        """
        if side == "BUY":
            if ask_price:
                # 尝试以卖一价买入（更快成交）
                order_price = ask_price
            elif bid_price:
                # 使用买一价 + 一个价位
                tick_size = 0.01 if current_price < 10 else (0.05 if current_price < 100 else 0.1)
                order_price = bid_price + tick_size
            else:
                # 使用当前价
                order_price = current_price

            # 限制不超过当前价+0.5%
            max_price = current_price * 1.005
            order_price = min(order_price, max_price)

        else:  # SELL
            if bid_price:
                # 尝试以买一价卖出（更快成交）
                order_price = bid_price
            elif ask_price:
                # 使用卖一价 - 一个价位
                tick_size = 0.01 if current_price < 10 else (0.05 if current_price < 100 else 0.1)
                order_price = ask_price - tick_size
            else:
                # 使用当前价
                order_price = current_price

            # 限制不低于当前价-0.5%
            min_price = current_price * 0.995
            order_price = max(order_price, min_price)

        logger.debug(f"  💰 下单价计算: {side}, ${order_price:.2f}")
        return order_price

    async def _send_buy_notification(
        self,
        symbol: str,
        signal: Dict,
        order: Dict,
        quantity: int,
        order_price: float,
        required_cash: float
    ):
        """发送买入通知到Slack"""
        try:
            signal_type = signal.get('type', 'BUY')
            score = signal.get('score', 0)
            indicators = signal.get('indicators', {})
            reasons = signal.get('reasons', [])
            strategy_name = signal.get('strategy', 'GENERAL')

            emoji_map = {
                'STRONG_BUY': '🚀',
                'BUY': '📈',
                'WEAK_BUY': '👍'
            }
            emoji = emoji_map.get(signal_type, '💰')

            # 构建技术指标信息
            indicators_text = f"📊 *技术指标*:\n"
            if 'rsi' in indicators:
                rsi = indicators['rsi']
                indicators_text += f"   • RSI: {rsi:.1f}"
                if rsi < 30:
                    indicators_text += " (超卖 ⬇️)\n"
                elif rsi > 70:
                    indicators_text += " (超买 ⬆️)\n"
                else:
                    indicators_text += "\n"

            if 'macd' in indicators and 'macd_signal' in indicators:
                macd = indicators['macd']
                macd_signal = indicators['macd_signal']
                macd_diff = macd - macd_signal
                indicators_text += f"   • MACD: {macd:.3f} | Signal: {macd_signal:.3f}\n"
                if macd_diff > 0:
                    indicators_text += f"   • MACD差值: +{macd_diff:.3f} (金叉 ✅)\n"

            if 'volume_ratio' in indicators:
                vol_ratio = indicators['volume_ratio']
                indicators_text += f"   • 成交量比率: {vol_ratio:.2f}x"
                if vol_ratio > 1.5:
                    indicators_text += " (放量 📈)\n"
                else:
                    indicators_text += "\n"

            # 构建买入原因
            reasons_text = ""
            if reasons:
                reasons_text = "\n💡 *买入理由*:\n"
                for reason in reasons:
                    reasons_text += f"   • {reason}\n"

            message = (
                f"{emoji} *开仓订单已提交*\n\n"
                f"📋 订单ID: `{order.get('order_id', 'N/A')}`\n"
                f"📊 标的: *{symbol}*\n"
                f"📘 策略: `{strategy_name}`\n"
                f"💯 信号类型: {signal_type}\n"
                f"⭐ 综合评分: *{score}/100*\n\n"
                f"💰 *交易信息*:\n"
                f"   • 数量: {quantity}股\n"
                f"   • 价格: ${order_price:.2f}\n"
                f"   • 总额: ${required_cash:.2f}\n\n"
                f"{indicators_text}\n"
                f"🎯 *风控设置*:\n"
                f"   • 止损位: ${signal.get('stop_loss', 0):.2f}\n"
                f"   • 止盈位: ${signal.get('take_profit', 0):.2f}\n"
            )

            if reasons:
                message += reasons_text

            await self.slack.send(message)

        except Exception as e:
            logger.warning(f"⚠️ 发送Slack通知失败: {e}")

    async def _send_capacity_notification(
        self,
        symbol: str,
        signal: Dict,
        price: float,
        available_cash: float,
        buy_power: float,
        reason: str
    ):
        """发送因资金/额度不足跳过下单的提示"""
        try:
            signal_type = signal.get('type', 'BUY')
            score = signal.get('score', 0)
            strategy_name = signal.get('strategy', 'GENERAL')
            message = (
                "⏸️ *买单跳过*\n\n"
                f"📊 标的: *{symbol}*\n"
                f"📘 策略: `{strategy_name}`\n"
                f"💡 信号类型: {signal_type} ({score}分)\n"
                f"💰 价格: ${price:.2f}\n"
                f"⚠️ 原因: {reason}\n\n"
                "📉 资金状态:\n"
                f"   • 可用资金: ${available_cash:,.2f}\n"
                f"   • 购买力: ${buy_power:,.2f}\n"
            )
            await self.slack.send(message)
        except Exception as e:
            logger.warning(f"⚠️ 发送额度不足通知失败: {e}")

    async def _send_sell_notification(
        self,
        symbol: str,
        signal: Dict,
        order: Dict,
        quantity: int,
        order_price: float
    ):
        """发送卖出通知到Slack（增强版：包含盈亏、持仓时长、技术指标）"""
        try:
            signal_type = signal.get('type', 'SELL')
            reason = signal.get('reason', '平仓')
            score = signal.get('score', 0)
            strategy_name = signal.get('strategy', 'GENERAL')

            emoji = "🛑" if "止损" in reason else ("🎯" if "止盈" in reason else "💵")

            # 基础信息
            message = (
                f"{emoji} *平仓订单已提交*\n\n"
                f"📋 订单ID: `{order.get('order_id', 'N/A')}`\n"
                f"📊 标的: *{symbol}*\n"
                f"📘 策略: `{strategy_name}`\n"
                f"💡 原因: {reason}\n"
                f"⭐ 评分: {score}/100\n\n"
            )

            # 交易信息（包含成本价）
            cost_price = signal.get('cost_price', 0)
            message += (
                f"💰 *交易信息*:\n"
                f"   • 数量: {quantity}股\n"
                f"   • 卖出价: ${order_price:.2f}\n"
            )

            if cost_price > 0:
                message += f"   • 成本价: ${cost_price:.2f}\n"

            message += f"   • 总额: ${order_price * quantity:.2f}\n"

            # 🔥 盈亏分析（如果有成本价）
            if cost_price > 0:
                profit_amount = (order_price - cost_price) * quantity
                profit_pct = (order_price - cost_price) / cost_price * 100
                profit_emoji = "📈" if profit_pct > 0 else ("📉" if profit_pct < 0 else "➖")

                message += (
                    f"\n{profit_emoji} *盈亏分析*:\n"
                    f"   • 收益率: {profit_pct:+.2f}%\n"
                    f"   • 盈亏金额: ${profit_amount:+,.2f}\n"
                )

            # 🔥 持仓时长（如果有买入时间）
            entry_time_str = signal.get('entry_time')
            if entry_time_str:
                try:
                    from datetime import datetime
                    entry_time = datetime.fromisoformat(entry_time_str)
                    holding_duration = datetime.now() - entry_time

                    hours = holding_duration.total_seconds() / 3600
                    if hours < 1:
                        holding_text = f"{hours * 60:.0f}分钟"
                    elif hours < 24:
                        holding_text = f"{hours:.1f}小时"
                    else:
                        holding_text = f"{hours / 24:.1f}天"

                    message += f"   • 持仓时长: {holding_text}\n"
                except Exception as e:
                    logger.warning(f"解析持仓时长失败: {e}")

            # 🔥 技术指标（如果是智能止盈）
            if signal_type in ['SMART_TAKE_PROFIT', 'EARLY_TAKE_PROFIT', 'STRONG_SELL', 'SELL']:
                indicators = signal.get('indicators', {})
                if indicators:
                    rsi = indicators.get('rsi')
                    macd = indicators.get('macd')
                    macd_signal = indicators.get('macd_signal')

                    message += f"\n📊 *技术指标*:\n"

                    if rsi is not None:
                        rsi_status = "超买" if rsi > 70 else ("超卖" if rsi < 30 else "正常")
                        message += f"   • RSI: {rsi:.1f} ({rsi_status})\n"

                    if macd is not None and macd_signal is not None:
                        macd_diff = macd - macd_signal
                        macd_status = "金叉" if macd_diff > 0 else "死叉"
                        message += f"   • MACD: {macd:.3f} | Signal: {macd_signal:.3f}\n"
                        message += f"   • MACD差值: {macd_diff:+.3f} ({macd_status})\n"

            # 🔥 卖出评分详情（如果有）
            exit_reasons = signal.get('exit_score_details', [])
            if exit_reasons and isinstance(exit_reasons, list):
                message += f"\n💡 *卖出依据*:\n"
                for idx, reason_item in enumerate(exit_reasons[:5], 1):  # 最多显示5条
                    message += f"   {idx}. {reason_item}\n"

            await self.slack.send(message)

        except Exception as e:
            logger.warning(f"⚠️ 发送Slack通知失败: {e}")

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

    async def _send_failure_notification(
        self,
        symbol: str,
        signal: Dict,
        error: str
    ):
        """发送订单执行失败通知到Slack（智能显示：资金不足时显示完整技术分析）"""
        try:
            signal_type = signal.get('type', 'BUY')
            score = signal.get('score', 0)
            price = signal.get('price', 0)

            # 判断是否为资金不足类错误
            is_insufficient_funds = any(keyword in error.lower() for keyword in [
                '可买数量为0',
                '资金不足',
                'insufficient',
                'buying power',
                'cash',
                'fallback也失败'
            ])

            # 如果是资金不足错误，显示完整的信号技术分析
            if is_insufficient_funds and signal:
                lines = [
                    f"❌ **订单执行失败 - 资金不足**",
                    f"",
                    f"📊 **买入信号分析**",
                    f"• 标的：{symbol} | 价格：${price:.2f} | 评分：{score}/100",
                ]

                # 添加技术分析
                tech_lines = self._format_signal_technical_analysis(signal)
                if tech_lines:
                    lines.extend(tech_lines)

                # 添加错误信息
                lines.extend([
                    f"",
                    f"⚠️ **错误**：{error}",
                    f"",
                    f"💡 **建议**：请检查账户购买力或等待挪仓机会"
                ])

                message = "\n".join(lines)
            else:
                # 其他错误，保持简短通知
                message = (
                    f"❌ **订单执行失败**\n"
                    f"标的: {symbol}\n"
                    f"类型: {signal_type}\n"
                    f"评分: {score}\n"
                    f"价格: ${price:.2f}\n"
                    f"错误: {error}\n"
                )

            await self.slack.send(message)

        except Exception as e:
            logger.warning(f"⚠️ 发送失败通知到Slack时出错: {e}")

    async def _send_position_rotation_analysis(
        self,
        new_signal: Dict,
        needed_amount: float,
        available_cash: float,
        all_positions: list,
        suggested_sales: list,
        rotation_allowed: bool = True
    ):
        """
        发送详细的持仓分析和换仓建议到Slack

        Args:
            new_signal: 新信号信息
            needed_amount: 需要的资金
            available_cash: 可用现金
            all_positions: 所有持仓分析结果
            suggested_sales: 建议卖出的持仓
            rotation_allowed: 是否允许自动触发智能轮换
        """
        if not self.slack:
            return

        symbol = new_signal.get('symbol', 'N/A')
        price = new_signal.get('price', 0)
        score = new_signal.get('score', 0)

        # 计算总市值和总盈亏
        total_market_value = sum(p['market_value'] for p in all_positions)
        total_pnl = sum(p['pnl'] for p in all_positions)
        base_cost = total_market_value - total_pnl
        total_pnl_pct = (total_pnl / base_cost * 100) if base_cost > 0 else 0
        funding_gap = max(needed_amount - available_cash, 0)

        # 构建新信号信息
        signal_section = (
            f"## 📈 新买入信号\n"
            f"• **标的:** {symbol}\n"
            f"• **价格:** ${price:.2f}\n"
            f"• **评分:** {score}/100\n"
            f"• **需要资金:** ${needed_amount:.2f}\n"
            f"• **可用现金:** ${available_cash:.2f}\n"
            f"• **资金缺口:** ${funding_gap:.2f}"
        )

        # 构建持仓概览
        overview_section = (
            f"\n## 💼 持仓概览\n"
            f"• **持仓数量:** {len(all_positions)}个\n"
            f"• **总市值:** ${total_market_value:,.2f}\n"
            f"• **总盈亏:** ${total_pnl:,.2f} ({total_pnl_pct:+.2f}%)"
        )

        # 构建建议卖出的持仓
        if suggested_sales:
            cumulative_freed = sum(p['potential_freed'] for p in suggested_sales)
            shortfall_after_rotation = max(needed_amount - cumulative_freed, 0)
            sales_section = f"\n## 🔴 建议换仓 ({len(suggested_sales)}个)\n\n"

            for i, pos in enumerate(suggested_sales[:5], 1):  # 最多显示5个
                pnl_emoji = "📈" if pos['pnl'] > 0 else "📉"
                sales_section += (
                    f"### {i}. {pos['symbol']} {pos['recommendation']}\n"
                    f"• **持仓:** {pos['quantity']}股 @ ${pos['cost_price']:.2f}\n"
                    f"• **现价:** ${pos['current_price']:.2f}\n"
                    f"• **盈亏:** {pnl_emoji} ${pos['pnl']:,.2f} ({pos['pnl_pct']:+.2f}%)\n"
                    f"• **市值:** ${pos['market_value']:,.2f}\n"
                    f"• **持有:** {pos['hold_hours']:.1f}小时\n"
                    f"• **评分:** {pos['rotation_score']}/100\n"
                    f"• **理由:** {pos['reason']}\n\n"
                )

            sales_section += (
                f"**预计释放资金:** ${cumulative_freed:,.2f} | 缺口: ${shortfall_after_rotation:.2f}"
            )
            if not rotation_allowed:
                sales_section += "\n_信号评分不足，需人工确认是否执行这些换仓建议_\n"
        else:
            if rotation_allowed:
                sales_section = "\n## 🟢 持仓质量分析\n所有持仓质量较好，不建议此时换仓"
            else:
                sales_section = (
                    "\n## 🟢 持仓质量分析\n"
                    "信号评分不足，系统不会自动换仓；以下持仓供人工参考"
                )

        # 构建其他持仓（建议保留的）
        keep_positions = [p for p in all_positions if p not in suggested_sales]
        if keep_positions:
            keep_section = f"\n## 🟢 建议保留 ({len(keep_positions)}个)\n\n"
            for i, pos in enumerate(keep_positions[:5], 1):  # 最多显示5个
                pnl_emoji = "📈" if pos['pnl'] > 0 else "📉"
                keep_section += (
                    f"**{i}. {pos['symbol']}** {pos['recommendation']}\n"
                    f"盈亏: {pnl_emoji} {pos['pnl_pct']:+.2f}%, "
                    f"评分: {pos['rotation_score']}/100, "
                    f"理由: {pos['reason']}\n\n"
                )
            if len(keep_positions) > 5:
                keep_section += f"_...还有{len(keep_positions)-5}个持仓_\n"
        else:
            keep_section = ""

        # 构建决策建议
        if suggested_sales:
            cumulative_freed = sum(p['potential_freed'] for p in suggested_sales)
            shortfall_after_rotation = max(needed_amount - cumulative_freed, 0)
            if rotation_allowed:
                if shortfall_after_rotation <= 0:
                    decision_section = (
                        f"\n## 💡 决策建议\n"
                        f"✅ **可以换仓:** 建议卖出上述{len(suggested_sales)}个持仓\n"
                        f"• 预计释放: ${cumulative_freed:,.2f}\n"
                        f"• 足够买入: {symbol} (需${needed_amount:.2f})\n"
                        f"• 系统将自动尝试智能轮换"
                    )
                else:
                    decision_section = (
                        f"\n## 💡 决策建议\n"
                        f"⚠️ **资金仍不足:** 即使卖出建议持仓\n"
                        f"• 预计释放: ${cumulative_freed:,.2f}\n"
                        f"• 仍缺: ${shortfall_after_rotation:,.2f}\n"
                        f"• 建议: 等待更好时机或手动调整"
                    )
            else:
                decision_section = (
                    f"\n## 💡 决策建议\n"
                    f"⚠️ **信号评分不足，系统不会自动换仓**\n"
                    f"• 预估可释放: ${cumulative_freed:,.2f}\n"
                    f"• 资金缺口: ${shortfall_after_rotation:,.2f}\n"
                    f"• 建议: 手动评估是否需要卖出以腾出购买力"
                )
        else:
            if rotation_allowed:
                decision_section = (
                    f"\n## 💡 决策建议\n"
                    f"❌ **不建议换仓:** 当前持仓质量较好\n"
                    f"• 新信号评分: {score}/100\n"
                    f"• 建议: 等待高质量卖出信号或更优买入机会"
                )
            else:
                decision_section = (
                    f"\n## 💡 决策建议\n"
                    f"❌ **信号评分不足，系统不会自动换仓**\n"
                    f"• 新信号评分: {score}/100 (<60)\n"
                    f"• 建议: 若需腾出购买力，请手动评估上述持仓"
                )

        # 组合完整消息
        message = (
            f"# 💰 资金不足 - 持仓分析报告\n\n"
            f"{signal_section}\n"
            f"{overview_section}\n"
            f"{sales_section}\n"
            f"{keep_section}"
            f"{decision_section}\n\n"
            f"---\n"
            f"_自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"
        )

        try:
            await self.slack.send(message)
            logger.info(f"  ✅ 已发送持仓分析报告到Slack/Discord")
        except Exception as e:
            logger.warning(f"  ⚠️ 发送持仓分析失败: {e}")

    async def _send_insufficient_funds_notification(
        self,
        signal: Dict,
        error_detail: str
    ):
        """
        发送资金不足的详细通知

        Args:
            signal: 信号数据
            error_detail: 详细错误信息（由预检查函数生成）
        """
        if not self.slack:
            return

        symbol = signal.get('symbol', 'N/A')
        signal_type = signal.get('type', 'N/A')
        score = signal.get('score', 0)
        price = signal.get('price', 0)
        retry_count = signal.get('retry_count', 0)

        # 解析错误详情，提取关键信息
        lines = error_detail.split('\n')
        summary_line = lines[0] if lines else error_detail

        message = (
            f"⚠️ **资金不足，无法执行订单**\n\n"
            f"**标的:** {symbol}\n"
            f"**类型:** {signal_type}\n"
            f"**评分:** {score}/100\n"
            f"**价格:** ${price:.2f}\n"
            f"**重试:** 第{retry_count}次\n\n"
            f"**详细说明:**\n{error_detail}\n\n"
            f"**当前状态:**\n"
            f"• 信号已延迟，等待资金释放后重试\n"
            f"• 系统将继续处理其他信号\n\n"
            f"**建议:**\n"
            f"• 查看是否有低质量持仓可以手动卖出\n"
            f"• 等待现有持仓达到止盈/止损自动释放资金\n"
            f"• 或等待更高质量的交易机会"
        )

        try:
            await self.slack.send(message)
            logger.debug(f"  ✅ 已发送资金不足详细通知到Slack/Discord")
        except Exception as e:
            logger.warning(f"  ⚠️ 发送通知失败: {e}")

    async def _send_insufficient_funds_final_notification(
        self,
        signal: Dict,
        retry_count: int,
        error_detail: str
    ):
        """
        发送资金不足最终放弃的通知

        Args:
            signal: 信号数据
            retry_count: 重试次数
            error_detail: 详细错误信息
        """
        if not self.slack:
            return

        symbol = signal.get('symbol', 'N/A')
        signal_type = signal.get('type', 'N/A')
        score = signal.get('score', 0)
        price = signal.get('price', 0)

        message = (
            f"❌ **放弃执行订单 - 资金持续不足**\n\n"
            f"**标的:** {symbol}\n"
            f"**类型:** {signal_type}\n"
            f"**评分:** {score}/100\n"
            f"**价格:** ${price:.2f}\n"
            f"**重试次数:** {retry_count}次\n\n"
            f"**原因:**\n"
            f"• 资金不足已重试{retry_count}次\n"
            f"• 资金状况未改善\n"
            f"• 系统已停止自动重试\n\n"
            f"**最后一次检查结果:**\n"
            f"{error_detail}\n\n"
            f"**后续操作:**\n"
            f"• ✅ 手动释放资金：卖出部分持仓\n"
            f"• ✅ 等待资金到账：充值或等待结算\n"
            f"• ✅ 手动重新生成信号：资金充足后重新扫描\n\n"
            f"_此信号已从队列移除，不会继续自动重试_"
        )

        try:
            await self.slack.send(message)
            logger.info(f"  ✅ 已发送最终放弃通知到Slack/Discord")
        except Exception as e:
            logger.warning(f"  ⚠️ 发送通知失败: {e}")

    async def _check_delayed_signals(self):
        """
        检查并唤醒延迟信号（卖出后资金可能充足）

        应在卖出订单完成后调用，让因资金不足延迟的信号立即可被处理
        """
        try:
            # 统计延迟信号数量
            delayed_count = await self.signal_queue.count_delayed_signals(
                account=self.settings.account_id
            )

            if delayed_count > 0:
                logger.info(
                    f"💰 卖出后资金释放，检测到{delayed_count}个延迟信号，尝试唤醒..."
                )

                # 唤醒延迟信号
                woken_count = await self.signal_queue.wake_up_delayed_signals(
                    account=self.settings.account_id
                )

                if woken_count > 0:
                    logger.success(
                        f"✅ 已唤醒{woken_count}个延迟信号，将在下次循环中处理"
                    )
            else:
                logger.debug("  无延迟信号需要唤醒")

        except Exception as e:
            logger.warning(f"⚠️ 检查延迟信号失败（不影响主流程）: {e}")

    async def _try_smart_rotation(
        self,
        signal: Dict,
        needed_amount: float,
        score_threshold: int = 15
    ) -> tuple[bool, float, list[dict]]:
        """
        尝试通过智能持仓轮换释放资金

        Args:
            signal: 新信号数据（包含symbol, score等）
            needed_amount: 需要释放的资金量
            score_threshold: 评分差异阈值（新信号评分需比持仓高这么多分）

        Returns:
            (成功与否, 实际释放的资金量, 卖出明细列表)
        """
        try:
            # 动态导入SmartPositionRotator
            import sys
            from pathlib import Path
            sys.path.append(str(Path(__file__).parent))

            from smart_position_rotation import SmartPositionRotator

            rotator = SmartPositionRotator()

            # 调用智能轮换释放资金
            logger.info(
                f"  📊 智能轮换参数: 新信号={signal.get('symbol', 'N/A')} "
                f"评分={signal.get('score', 0)}, 需要资金=${needed_amount:,.2f}, "
                f"评分差异阈值={score_threshold}分"
            )

            success, freed, sold_positions = await rotator.try_free_up_funds(
                needed_amount=needed_amount,
                new_signal=signal,
                trade_client=self.trade_client,
                quote_client=self.quote_client,
                score_threshold=score_threshold  # 🔥 使用动态阈值
            )

            if success:
                logger.success(f"  ✅ 智能轮换成功释放: ${freed:,.2f}")
            else:
                logger.warning(f"  ⚠️ 智能轮换未能释放足够资金: ${freed:,.2f}")

            return success, freed, sold_positions

        except ImportError as e:
            logger.error(f"❌ 导入SmartPositionRotator失败: {e}")
            logger.warning("⚠️ 智能轮换功能不可用，跳过轮换尝试")
            logger.info("   提示：检查 scripts/smart_position_rotation.py 是否存在")
            return False, 0.0, []
        except Exception as e:
            logger.error(f"❌ 智能轮换执行失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            logger.warning("   建议：检查持仓数据和行情数据是否正常")
            return False, 0.0, []

    async def _notify_rotation_result(
        self,
        new_signal: Dict,
        needed_amount: float,
        freed_amount: float,
        sold_positions: list[dict],
        success: bool
    ):
        """发送智能轮换结果到Slack，方便排查持仓被动调整"""
        if not self.slack or not sold_positions:
            return

        symbol = new_signal.get('symbol', 'N/A')
        score = new_signal.get('score', 0)
        status_emoji = "✅" if success else "⚠️"
        status_text = "资金释放成功" if success else "资金仍不足"

        details_lines = []
        for pos in sold_positions:
            line = f"   • {pos.get('symbol', 'N/A')}: 释放${pos.get('freed_amount', 0):,.2f}"
            if pos.get('score') is not None:
                line += f" (评分{pos['score']:.1f}, 差距{pos.get('score_diff', 0):.1f})"
            if pos.get('hold_minutes') is not None:
                line += f", 持有{pos['hold_minutes']:.1f}分钟"
            if pos.get('order_id'):
                line += f", 订单ID {pos['order_id']}"
            details_lines.append(line)

        message = (
            "♻️ *智能持仓轮换执行*\n\n"
            f"{status_emoji} {status_text}\n"
            f"📈 新信号: {symbol} ({score}分)\n"
            f"🎯 目标释放: ${needed_amount:,.2f}\n"
            f"💰 实际释放: ${freed_amount:,.2f}\n"
            "📉 卖出明细:\n"
            + "\n".join(details_lines)
        )

        try:
            await self.slack.send(message)
        except Exception as e:
            logger.warning(f"⚠️ 智能轮换通知发送失败: {e}")

    async def _mark_twap_execution(self, symbol: str, duration_seconds: int = 3600):
        """
        标记标的为TWAP执行中状态（防止重复信号）

        Args:
            symbol: 标的代码
            duration_seconds: 持续时间（秒），默认1小时
        """
        try:
            redis = await self.signal_queue._get_redis()
            redis_key = f"trading:twap_execution:{symbol}"
            await redis.setex(redis_key, duration_seconds, "1")
            logger.debug(f"  🔒 已标记TWAP执行: {symbol} (持续{duration_seconds}秒)")
        except Exception as e:
            logger.warning(f"  ⚠️ 标记TWAP执行失败: {e}")

    async def _unmark_twap_execution(self, symbol: str):
        """
        移除标的的TWAP执行中标记

        Args:
            symbol: 标的代码
        """
        try:
            redis = await self.signal_queue._get_redis()
            redis_key = f"trading:twap_execution:{symbol}"
            await redis.delete(redis_key)
            logger.debug(f"  🔓 已移除TWAP执行标记: {symbol}")
        except Exception as e:
            logger.warning(f"  ⚠️ 移除TWAP执行标记失败: {e}")

    async def _consume_batch(self) -> list[Dict]:
        """
        收集一批信号（动态批量决策窗口）

        策略：
        - 如果队列<=2个信号：快速通道，立即处理（0延迟）
        - 如果队列>2个信号：批次收集，等待signal_batch_window秒优化顺序
        - 止损/止盈信号（priority >= stop_loss_priority）始终立即执行

        Returns:
            list[Dict]: 信号列表，按score降序排列
        """
        import time

        # 🔥 动态决策：检查队列大小决定是否使用批次模式
        queue_size = await self.signal_queue.get_queue_size()

        batch = []
        batch_start = time.time()
        batch_window = self.settings.signal_batch_window
        batch_size = self.settings.signal_batch_size
        stop_loss_priority = self.settings.stop_loss_priority

        # 🔥 传递TTL配置
        signal_ttl = self.settings.signal_ttl_seconds
        max_delay = self.settings.max_delay_seconds

        # 🔥 快速通道：信号稀少时立即处理，不等待
        if queue_size <= 2:
            logger.debug(f"⚡ 快速通道: 队列仅{queue_size}个信号，立即处理（跳过批次等待）")
            batch_window = 0  # 不等待，立即收集
        else:
            logger.debug(f"📦 批次模式: 队列有{queue_size}个信号，使用批次收集（窗口={batch_window}秒）")

        consecutive_empty_attempts = 0  # 🔥 连续空尝试计数
        max_empty_attempts = 3  # 🔥 最多3次连续空尝试

        while len(batch) < batch_size:
            # 计算剩余等待时间
            elapsed = time.time() - batch_start
            remaining_time = batch_window - elapsed

            if remaining_time <= 0:
                # 时间窗口已满（快速通道时batch_window=0，立即触发）
                if batch_window == 0:
                    logger.debug(f"  ⚡ 快速通道：已收集{len(batch)}个信号，立即返回")
                else:
                    logger.debug(f"  ⏰ 批次收集窗口已满（{batch_window}秒）")
                break

            try:
                # 🔥 传递TTL参数给consume_signal
                # 🔥 快速通道时使用更短的超时时间（0.1秒）
                timeout = 0.1 if batch_window == 0 else min(remaining_time, 1.0)

                signal = await asyncio.wait_for(
                    self.signal_queue.consume_signal(
                        signal_ttl_seconds=signal_ttl,
                        max_delay_seconds=max_delay
                    ),
                    timeout=timeout
                )

                if signal:
                    consecutive_empty_attempts = 0  # 🔥 重置计数器

                    priority = signal.get('score', 0)
                    symbol = signal.get('symbol', 'N/A')
                    signal_type = signal.get('type', 'UNKNOWN')

                    logger.debug(f"  📥 收集到信号: {symbol} (type={signal_type}, score={priority})")

                    # 止损/止盈信号立即返回（优先级999）
                    if priority >= stop_loss_priority:
                        logger.info(f"  🚨 收到高优先级信号({priority}分)，立即执行: {symbol}")
                        batch.insert(0, signal)  # 插入到开头
                        break

                    batch.append(signal)
                else:
                    # 🔥 队列暂无可用信号（可能为空或都在延迟状态）
                    consecutive_empty_attempts += 1
                    delay_hint = getattr(self.signal_queue, "_last_delay_hint", None)

                    # 🔥 快速通道：第一次为空就立即退出
                    if batch_window == 0 and len(batch) == 0:
                        if delay_hint:
                            logger.debug(
                                f"  ⚡ 快速通道：队列信号均在延迟，"
                                f"最短还需等待{delay_hint:.0f}秒，立即返回"
                            )
                        else:
                            logger.debug(f"  ⚡ 快速通道：队列为空，立即返回")
                        break

                    if consecutive_empty_attempts >= max_empty_attempts:
                        # 🔥 连续多次为空，退出循环
                        if delay_hint:
                            logger.debug(
                                f"  💤 连续{consecutive_empty_attempts}次队列仅包含延迟信号，"
                                f"最短还需等待{delay_hint:.0f}秒，结束批次收集"
                            )
                        else:
                            logger.debug(
                                f"  💤 连续{consecutive_empty_attempts}次队列为空，"
                                f"结束批次收集"
                            )
                        break
                    else:
                        if delay_hint:
                            logger.debug(
                                f"  ⏳ 队列信号均未到重试时间，"
                                f"最短还需等待{delay_hint:.0f}秒（尝试{consecutive_empty_attempts}/{max_empty_attempts}）"
                            )
                        else:
                            logger.debug(
                                f"  ⏳ 队列暂为空（尝试{consecutive_empty_attempts}/{max_empty_attempts}）"
                            )

            except asyncio.TimeoutError:
                # 超时，也算作空尝试
                # 🔥 快速通道：超时立即退出
                if batch_window == 0:
                    logger.debug(f"  ⚡ 快速通道：超时，已收集{len(batch)}个信号，立即返回")
                    break

                if len(batch) > 0:
                    # 已有信号，继续等待看是否有更多信号
                    continue
                else:
                    # 无信号且时间未到
                    consecutive_empty_attempts += 1
                    delay_hint = getattr(self.signal_queue, "_last_delay_hint", None)
                    if consecutive_empty_attempts >= max_empty_attempts:
                        if delay_hint:
                            logger.debug(
                                f"  💤 连续{consecutive_empty_attempts}次超时且仅有延迟信号，"
                                f"最短还需等待{delay_hint:.0f}秒，结束批次收集"
                            )
                        else:
                            logger.debug(
                                f"  💤 连续{consecutive_empty_attempts}次超时，"
                                f"结束批次收集"
                            )
                        break
                    else:
                        if delay_hint:
                            logger.debug(
                                f"  ⏳ 超时未取到信号，队列最短等待{delay_hint:.0f}秒 "
                                f"（尝试{consecutive_empty_attempts}/{max_empty_attempts}）"
                            )
                        else:
                            logger.debug(
                                f"  ⏳ 超时未取到信号 "
                                f"（尝试{consecutive_empty_attempts}/{max_empty_attempts}）"
                            )
                    continue
            except Exception as e:
                logger.warning(f"  ⚠️ 消费信号时出错: {e}")
                break

        # 按score降序排序（高分优先）
        if batch:
            batch.sort(key=lambda x: x.get('score', 0), reverse=True)

            logger.info(
                f"📦 批次收集完成: {len(batch)}个信号, "
                f"分数范围=[{batch[-1].get('score', 0)}-{batch[0].get('score', 0)}]"
            )

            # 打印批次明细
            for idx, sig in enumerate(batch, 1):
                logger.info(
                    f"  #{idx} {sig.get('symbol', 'N/A')} - "
                    f"{sig.get('type', 'UNKNOWN')} ({sig.get('score', 0)}分)"
                )
        else:
            delay_hint = getattr(self.signal_queue, "_last_delay_hint", None)
            current_queue = await self.signal_queue.get_queue_size()
            if delay_hint and current_queue > 0:
                logger.debug(
                    f"  ⏳ 批次为空，队列中{current_queue}个信号尚未到重试时间，"
                    f"最短还需等待{delay_hint:.0f}秒"
                )
            else:
                logger.debug("  📦 批次为空，未收集到信号")

        return batch

    async def _requeue_remaining(
        self,
        remaining_signals: list[Dict],
        reason: str = "资金不足"
    ) -> int:
        """
        将剩余信号重新入队（延迟重试）

        Args:
            remaining_signals: 剩余的信号列表
            reason: 重新入队原因

        Returns:
            int: 成功重新入队的数量
        """
        if not remaining_signals:
            return 0

        logger.info(
            f"♻️ 重新入队{len(remaining_signals)}个信号（{reason}）"
        )

        requeued_count = 0

        for signal in remaining_signals:
            symbol = signal.get('symbol', 'N/A')
            score = signal.get('score', 0)

            # 检查重试次数
            retry_count = signal.get('retry_count', 0)
            if retry_count >= self.settings.funds_retry_max:
                logger.warning(
                    f"  ⚠️ {symbol} 已达最大重试次数({self.settings.funds_retry_max})，"
                    f"标记为完成"
                )
                await self.signal_queue.mark_signal_completed(signal)
                continue

            # 增加重试计数
            signal['retry_count'] = retry_count + 1

            # 🔥 智能退避：延迟时间随重试次数增加
            # 第1次: 5分钟，第2次: 10分钟，第3次: 15分钟...
            delay_minutes = self.settings.funds_retry_delay * signal['retry_count']

            # 🔥 限制最大延迟不超过30分钟
            delay_minutes = min(delay_minutes, 30)

            # 延迟重新入队
            success = await self.signal_queue.requeue_with_delay(
                signal,
                delay_minutes=delay_minutes,
                priority_penalty=20  # 每次重试降低20分
            )

            if success:
                requeued_count += 1
                logger.info(
                    f"  ✅ {symbol} 已重新入队（第{signal['retry_count']}次重试，"
                    f"{delay_minutes}分钟后重试，分数{score}→{score-20}）"
                )

                # 🔥 新增：高分信号延迟通知（只在首次延迟时通知）
                if score >= 60 and signal['retry_count'] == 1 and self.slack and reason == "资金不足":
                    try:
                        # 获取账户信息
                        try:
                            account = await self.trade_client.get_account()
                            currency = "HKD" if ".HK" in symbol else "USD"
                            cash = float(account["cash"].get(currency, 0))
                            power = float(account.get("buy_power", {}).get(currency, 0))
                        except:
                            currency = "HKD" if ".HK" in symbol else "USD"
                            cash = power = 0.0

                        # 估算所需资金（简单估算）
                        current_price = signal.get('price', 0)
                        lot_size = 100 if ".HK" in symbol else 1
                        estimated_need = current_price * lot_size if current_price > 0 else 0

                        # 获取信号原因
                        reason_text = signal.get('reason', '无')
                        if len(reason_text) > 200:
                            reason_text = reason_text[:200] + "..."

                        high_signal_message = (
                            f"🎯 **高分信号延迟处理**\n\n"
                            f"⚠️ 评分{score}分的优质信号因资金不足被延迟\n\n"
                            f"📊 **信号详情:**\n"
                            f"   • 标的: {symbol}\n"
                            f"   • 评分: {score}/100 (高质量信号)\n"
                            f"   • 类型: {signal.get('type', 'BUY')}\n"
                            f"   • 价格: ${current_price:.2f}\n"
                            f"   • 原因: {reason_text}\n\n"
                            f"⏰ **延迟信息:**\n"
                            f"   • 原因: 资金不足\n"
                            f"   • 预计重试: {delay_minutes}分钟后\n"
                            f"   • 重试次数: 1/{self.settings.funds_retry_max}\n\n"
                            f"💰 **账户状态 ({currency}):**\n"
                            f"   • 现金: ${cash:,.2f}\n"
                            f"   • 购买力: ${power:,.2f}\n"
                        )

                        if estimated_need > 0:
                            high_signal_message += f"   • 估算需要: ${estimated_need:,.2f}\n"

                        high_signal_message += (
                            f"\n💡 **可选操作:**\n"
                            f"   • 手动下单（如果认为机会重要）\n"
                            f"   • 卖出部分持仓释放资金\n"
                            f"   • 等待自动重试（共{self.settings.funds_retry_max}次机会）"
                        )

                        await self.slack.send(high_signal_message)
                        logger.info(f"  📨 已发送高分信号延迟通知: {symbol} ({score}分)")

                    except Exception as e:
                        logger.warning(f"⚠️ 发送高分信号通知失败: {e}")

            else:
                logger.error(f"  ❌ {symbol} 重新入队失败")

        # 🔥 发送Slack通知：资金不足导致信号延迟
        if requeued_count > 0 and self.slack and reason == "资金不足":
            try:
                # 获取账户信息用于通知
                try:
                    account = await self.trade_client.get_account()
                    hkd_cash = float(account["cash"].get("HKD", 0))
                    usd_cash = float(account["cash"].get("USD", 0))
                    hkd_power = float(account.get("buy_power", {}).get("HKD", 0))
                    usd_power = float(account.get("buy_power", {}).get("USD", 0))
                except:
                    hkd_cash = usd_cash = hkd_power = usd_power = 0.0

                # 构建延迟信号列表（去重：同一标的只显示一次）
                seen_symbols = set()
                signals_list = []

                for sig in remaining_signals:
                    symbol = sig.get('symbol', 'N/A')

                    # 跳过已显示的标的
                    if symbol in seen_symbols:
                        continue

                    # 达到显示上限，停止添加
                    if len(signals_list) >= 5:
                        break

                    seen_symbols.add(symbol)
                    score = sig.get('score', 0)
                    retry_count = sig.get('retry_count', 0)
                    delay_min = min(self.settings.funds_retry_delay * retry_count, 30)
                    signals_list.append(f"   • {symbol} (评分{score}, {delay_min}分钟后重试)")

                # 计算未显示的唯一标的数量
                total_unique_symbols = len(set(s.get('symbol') for s in remaining_signals))
                more_count = total_unique_symbols - len(signals_list)

                if more_count > 0:
                    signals_list.append(f"   • ... 还有{more_count}个标的")

                message = (
                    f"⚠️ **资金不足 - {requeued_count}个信号延迟处理**\n\n"
                    f"📊 **当前账户状态:**\n"
                    f"   • HKD现金: ${hkd_cash:,.2f}\n"
                    f"   • HKD购买力: ${hkd_power:,.2f}\n"
                    f"   • USD现金: ${usd_cash:,.2f}\n"
                    f"   • USD购买力: ${usd_power:,.2f}\n\n"
                    f"⏰ **延迟信号列表:**\n"
                    + "\n".join(signals_list) + "\n\n"
                    f"💡 **建议:** 卖出部分持仓释放资金，或等待延迟信号自动重试"
                )

                await self.slack.send(message)
            except Exception as e:
                logger.warning(f"⚠️ 发送资金不足通知失败: {e}")

        return requeued_count

    async def _execute_staged_buy(
        self,
        signal: Dict,
        total_budget: float,
        current_price: float
    ) -> tuple[int, float]:
        """
        分批建仓策略（根据信号强度决定分批数量）

        Args:
            signal: 信号数据
            total_budget: 总预算
            current_price: 当前价格

        Returns:
            (总成交数量, 平均价格)
        """
        score = signal.get('score', 0)
        symbol = signal['symbol']

        # 根据信号强度决定建仓策略
        if score >= 80:
            # 极强信号：一次性建仓（信号强，仓位重）
            stages = [(1.0, "全仓")]
            logger.info(f"  📊 建仓策略: 极强信号({score}分)，一次性全仓建仓")
        elif score >= 60:
            # 强信号：分两批（60% + 40%）
            stages = [(0.6, "首批"), (0.4, "加仓")]
            logger.info(f"  📊 建仓策略: 强信号({score}分)，分2批建仓（60%+40%）")
        else:
            # BUY信号（45-59分）：一次性建仓（仓位本就很小5-12%，无需分批）
            stages = [(1.0, "试探仓")]
            logger.info(f"  📊 建仓策略: 一般信号({score}分)，一次性试探建仓（仓位小）")

        total_filled = 0
        total_value = 0.0

        for idx, (stage_pct, stage_name) in enumerate(stages):
            stage_budget = total_budget * stage_pct

            # 计算本批次数量
            lot_size = await self.lot_size_helper.get_lot_size(symbol, self.quote_client)
            quantity = self.lot_size_helper.calculate_order_quantity(
                symbol, stage_budget, current_price, lot_size
            )

            if quantity <= 0:
                logger.warning(f"  ⚠️ {stage_name}阶段预算不足，跳过")
                continue

            logger.info(
                f"  📈 {stage_name}阶段: 预算=${stage_budget:,.2f}, "
                f"数量={quantity}股 ({quantity//lot_size}手)"
            )

            # 执行订单（使用TWAP策略）
            order_request = OrderRequest(
                symbol=symbol,
                side="BUY",
                quantity=quantity,
                order_type="LIMIT",
                limit_price=current_price,
                strategy=ExecutionStrategy.TWAP,
                urgency=5,
                max_slippage=0.01,
                signal=signal,
                metadata={"stage": stage_name, "stage_pct": stage_pct, "stage_num": idx + 1}
            )

            try:
                result = await self.smart_router.execute_order(order_request)

                if result.success and result.filled_quantity > 0:
                    total_filled += result.filled_quantity
                    total_value += result.filled_quantity * result.average_price

                    logger.success(
                        f"  ✅ {stage_name}阶段成交: {result.filled_quantity}股 @ "
                        f"${result.average_price:.2f}"
                    )

                    # 如果不是最后一批，等待一段时间观察行情
                    if idx < len(stages) - 1:
                        wait_minutes = self.stage_interval_minutes
                        logger.info(f"  ⏳ 等待{wait_minutes}分钟后评估是否继续加仓...")
                        await asyncio.sleep(wait_minutes * 60)

                        # 重新获取当前价格
                        try:
                            quote = await self.quote_client.get_realtime_quote([symbol])
                            if quote and len(quote) > 0:
                                new_price = float(quote[0].last_done)
                                price_change_pct = (new_price - current_price) / current_price * 100

                                logger.info(
                                    f"  📊 价格变化: ${current_price:.2f} → ${new_price:.2f} "
                                    f"({price_change_pct:+.2f}%)"
                                )

                                # 如果价格涨幅过大（>3%），可能不适合继续加仓
                                if price_change_pct > 3:
                                    logger.warning(
                                        f"  ⚠️ 价格涨幅较大({price_change_pct:+.2f}%)，"
                                        f"取消后续加仓"
                                    )
                                    break

                                # 更新当前价格
                                current_price = new_price
                        except Exception as e:
                            logger.error(f"  ❌ 获取最新价格失败: {e}，继续使用原价格")
                else:
                    logger.error(f"  ❌ {stage_name}阶段失败: {result.error_message}")
                    # 如果第一批就失败，直接退出
                    if idx == 0:
                        break
                    # 非第一批失败，尝试继续
                    continue

            except Exception as e:
                logger.error(f"  ❌ {stage_name}阶段异常: {e}")
                if idx == 0:
                    break
                continue

        # 计算平均价格
        avg_price = total_value / total_filled if total_filled > 0 else 0

        if total_filled > 0:
            logger.success(
                f"  🎯 分批建仓完成: 总计成交{total_filled}股, "
                f"平均价格${avg_price:.2f}"
            )
        else:
            logger.error(f"  ❌ 分批建仓失败: 所有批次均未成交")

        return total_filled, avg_price


async def main(account_id: str | None = None):
    """
    主函数

    Args:
        account_id: 账号ID，如果指定则从configs/accounts/{account_id}.env加载配置
    """
    executor = OrderExecutor(account_id=account_id)

    try:
        await executor.run()
    except Exception as e:
        logger.error(f"❌ 订单执行器运行失败: {e}")
        import traceback
        logger.error(traceback.format_exc())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="订单执行器 (Order Executor) - 从Redis队列消费交易信号并执行订单",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 使用默认配置（.env文件）
  python3 scripts/order_executor.py

  # 使用指定账号配置
  python3 scripts/order_executor.py --account-id paper_001
  python3 scripts/order_executor.py --account-id live_001
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
║               订单执行器 (Order Executor)                     ║
╠══════════════════════════════════════════════════════════════╣
║  功能:                                                         ║
║  • 从Redis队列消费交易信号                                    ║
║  • 执行风控检查                                               ║
║  • 提交订单到LongPort                                         ║
║  • 发送Slack通知                                              ║
║  • 记录止损止盈                                               ║
╚══════════════════════════════════════════════════════════════╝
    """)

    if args.account_id:
        print(f"📌 使用账号配置: {args.account_id}")
        print(f"📁 配置文件: configs/accounts/{args.account_id}.env\n")
    else:
        print(f"📌 使用默认配置: .env\n")

    asyncio.run(main(account_id=args.account_id))
