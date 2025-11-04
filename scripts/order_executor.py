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
from typing import Dict, Optional

# 添加项目根目录到路径
sys.path.append(str(Path(__file__).parent.parent))

from longport import openapi
from longport_quant.config import get_settings
from longport_quant.execution.client import LongportTradingClient
from longport_quant.execution.smart_router import SmartOrderRouter, OrderRequest, ExecutionStrategy
from longport_quant.execution.risk_assessor import RiskAssessor
from longport_quant.risk.regime import RegimeClassifier
from longport_quant.risk.rebalancer import RegimeRebalancer
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
        self.max_position_size_pct = 0.40  # 最大仓位40%（极强信号专用，80-100分）
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

        # 【新增】风险评估器 - 智能决策备份条件单
        self.risk_assessor = RiskAssessor(config=self.settings.backup_orders)

        # 【新增】Redis持仓管理器 - 跨进程共享持仓状态
        self.position_manager = RedisPositionManager(
            redis_url=self.settings.redis_url,
            key_prefix="trading"
        )

        # 持仓追踪
        self.positions_with_stops = {}  # {symbol: {entry_price, stop_loss, take_profit}}

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
                self.smart_router = SmartOrderRouter(trade_ctx, db_manager)
                logger.info("✅ SmartOrderRouter已初始化（支持TWAP/VWAP算法订单）")

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
                                logger.warning(f"  ⚠️ [{idx}/{len(batch)}] {symbol}: 资金不足")
                                logger.info(f"  💡 策略：仅延迟当前信号，继续处理后续{len(batch)-idx}个信号")

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

    async def _execute_buy_order(self, signal: Dict):
        """执行买入订单"""
        symbol = signal['symbol']
        signal_type = signal['type']
        current_price = signal.get('price', 0)
        score = signal.get('score', 0)

        # 1. 获取账户信息
        try:
            account = await self.trade_client.get_account()
        except Exception as e:
            logger.error(f"❌ 获取账户信息失败: {e}")
            raise

        # 2. 弱买入信号过滤
        if signal_type == "WEAK_BUY" and score < 35:
            logger.info(f"  ⏭️ 跳过弱买入信号 (评分: {score})")
            return  # 直接返回，信号会被标记为完成

        # 3. 资金检查
        currency = "HKD" if ".HK" in symbol else "USD"
        available_cash = account["cash"].get(currency, 0)
        buy_power = account.get("buy_power", {}).get(currency, 0)
        remaining_finance = account.get("remaining_finance", {}).get(currency, 0)

        # 显示购买力和融资额度信息
        logger.debug(
            f"  💰 {currency} 资金状态 - 可用: ${available_cash:,.2f}, "
            f"购买力: ${buy_power:,.2f}, 剩余融资额度: ${remaining_finance:,.2f}"
        )

        if available_cash < 0:
            logger.error(
                f"  ❌ {symbol}: 资金异常（显示为负数: ${available_cash:.2f}）\n"
                f"     可能原因：融资账户或数据错误"
            )
            if account.get('buy_power', {}).get(currency, 0) > 1000:
                logger.info(f"  💳 使用购买力进行交易")
            else:
                logger.warning(f"  ⏭️ 账户资金异常，跳过交易")
                raise InsufficientFundsError(f"账户资金异常（显示为负数: ${available_cash:.2f}）")

        # 4. 计算动态预算
        dynamic_budget = self._calculate_dynamic_budget(account, signal)

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

                # 重新获取账户信息
                try:
                    account = await self.trade_client.get_account()
                    available_cash = account["cash"].get(currency, 0)

                    if available_cash >= required_cash:
                        logger.success(f"  💰 轮换后可用资金: ${available_cash:,.2f}，继续执行订单")

                        # 重新计算动态预算和购买数量
                        dynamic_budget = self._calculate_dynamic_budget(account, signal)

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

            # 发送失败通知到 Slack
            if self.slack:
                await self._send_failure_notification(
                    symbol=symbol,
                    signal=signal,
                    error=str(e)
                )

            raise

    async def _regime_updater(self):
        """周期性更新市场状态（牛/熊/震荡）。"""
        interval = max(3, int(getattr(self.settings, 'regime_update_interval_minutes', 10))) * 60
        while True:
            try:
                # 获取市场状态（根据交易时段自动过滤指数）
                res = await self.regime_classifier.classify(self.quote_client, filter_by_market=True)

                # 如果非交易时段，跳过通知
                if res.active_market == "NONE":
                    logger.debug(f"⏰ 非交易时段，跳过Regime检查")
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

                # 获取账户信息
                try:
                    account = await self.trade_client.get_account()
                    hkd_cash = account["cash"].get("HKD", 0)
                    usd_cash = account["cash"].get("USD", 0)
                    hkd_power = account.get("buy_power", {}).get("HKD", 0)
                    usd_power = account.get("buy_power", {}).get("USD", 0)
                except:
                    hkd_cash = usd_cash = hkd_power = usd_power = 0

                # 队列长时间为空的警告（连续3小时）
                if queue_size == 0:
                    consecutive_empty_count += 1
                    if consecutive_empty_count >= 3 and (time.time() - last_empty_alert_time) > 10800:
                        # 3小时警告
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

                    if self.slack:
                        await self.slack.send(message)

                logger.debug(f"队列状态摘要已发送: {queue_size}个待处理, {delayed_count}个延迟")

            except Exception as e:
                logger.warning(f"⚠️ 发送队列状态摘要失败: {e}")

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

        # 2. 弱买入信号过滤
        if signal_type == "WEAK_BUY" and score < 35:
            logger.info(f"  ⏭️ 跳过弱买入信号 (评分: {score})")
            return  # 直接返回，信号会被标记为完成

        # 3. 资金检查
        currency = "HKD" if ".HK" in symbol else "USD"
        available_cash = account["cash"].get(currency, 0)
        buy_power = account.get("buy_power", {}).get(currency, 0)
        remaining_finance = account.get("remaining_finance", {}).get(currency, 0)

        # 显示购买力和融资额度信息
        logger.debug(
            f"  💰 {currency} 资金状态 - 可用: ${available_cash:,.2f}, "
            f"购买力: ${buy_power:,.2f}, 剩余融资额度: ${remaining_finance:,.2f}"
        )

        if available_cash < 0:
            logger.error(
                f"  ❌ {symbol}: 资金异常（显示为负数: ${available_cash:.2f}）\n"
                f"     可能原因：融资账户或数据错误"
            )
            if account.get('buy_power', {}).get(currency, 0) > 1000:
                logger.info(f"  💳 使用购买力进行交易")
            else:
                logger.warning(f"  ⏭️ 账户资金异常，跳过交易")
                raise InsufficientFundsError(f"账户资金异常（显示为负数: ${available_cash:.2f}）")

        # 4. 计算动态预算（支持信号内指定预算覆盖）
        dynamic_budget = self._calculate_dynamic_budget(account, signal)
        try:
            # 优先使用信号指定的名义额预算（单位：币种金额）
            budget_notional = signal.get('budget_notional')
            if isinstance(budget_notional, (int, float)) and budget_notional > 0:
                dynamic_budget = float(budget_notional)
            else:
                # 次优先：基于账户净资产的百分比预算
                budget_pct = signal.get('budget_pct')
                if isinstance(budget_pct, (int, float)) and budget_pct > 0:
                    net_assets = account.get("net_assets", {}).get(
                        "HKD" if ".HK" in symbol else "USD", 0
                    )
                    if net_assets > 0:
                        dynamic_budget = max(0.0, float(net_assets) * float(budget_pct))
        except Exception as e:
            logger.debug(f"应用策略预算失败（忽略继续）: {e}")

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
        min_required_cash = lot_size * current_price

        # 8.1 小额订单过滤（最小下单金额），仅对BUY生效
        try:
            if symbol.endswith('.HK'):
                min_notional = float(getattr(self.settings, 'min_order_notional_hkd', 0.0) or 0.0)
            else:
                min_notional = float(getattr(self.settings, 'min_order_notional_usd', 0.0) or 0.0)

            if min_notional > 0 and required_cash < min_notional:
                logger.info(
                    f"  ⏭️ 小额订单过滤: 预计成交额 ${required_cash:.2f} < 最小金额 ${min_notional:.2f}，跳过以节省手续费"
                )
                return
        except Exception as e:
            logger.debug(f"小额订单过滤检查失败（忽略继续）: {e}")

        if quantity <= 0 or dynamic_budget < min_required_cash:
            # 尝试使用交易端预估接口获取可买数量（考虑融资/购买力）
            estimated_quantity = await self._estimate_available_quantity(
                symbol=symbol,
                price=current_price,
                lot_size=lot_size,
                currency=currency
            )

            if estimated_quantity > 0:
                quantity = estimated_quantity
                num_lots = quantity // lot_size
                required_cash = current_price * quantity
                dynamic_budget = required_cash

                logger.info(
                    f"  ✅ 通过交易接口预估可买数量: {quantity}股 "
                    f"({num_lots}手)，估算资金需求=${required_cash:.2f}"
                )

                if dynamic_budget >= min_required_cash:
                    logger.debug("  🔄 预估结果满足最小手数，跳过持仓轮换")
            else:
                logger.warning(
                    f"  ⚠️ {symbol}: 预估最大可买数量为0"
                )

                # 🔥 分层挪仓策略：根据信号评分决定是否尝试挪仓
                needed_amount = min_required_cash - available_cash
                rotation_attempted = False
                rotation_success = False

                logger.info(
                    f"  📊 挪仓决策分析:\n"
                    f"     • 信号评分: {score}分\n"
                    f"     • 所需金额: ${needed_amount:.2f}\n"
                    f"     • 可用资金: ${available_cash:.2f}"
                )

                if score >= 70:
                    # 高分信号（70+）：积极挪仓，评分差10分即可
                    logger.info(
                        f"  🔥 高分信号({score}分 ≥ 70)：积极挪仓（评分差≥10分）"
                    )
                    rotation_attempted = True
                    rotation_success, freed_amount, rotation_details = await self._try_smart_rotation(
                        signal, needed_amount, score_threshold=10
                    )
                elif score >= 60:
                    # 中分信号（60-70）：适度挪仓，评分差15分
                    logger.info(
                        f"  ⚡ 中分信号({score}分 ∈ [60,70))：适度挪仓（评分差≥15分）"
                    )
                    rotation_attempted = True
                    rotation_success, freed_amount, rotation_details = await self._try_smart_rotation(
                        signal, needed_amount, score_threshold=15
                    )
                elif score >= 55:
                    # 低分信号（55-60）：保守挪仓，评分差20分
                    logger.info(
                        f"  💡 低分信号({score}分 ∈ [55,60))：保守挪仓（评分差≥20分）"
                    )
                    rotation_attempted = True
                    rotation_success, freed_amount, rotation_details = await self._try_smart_rotation(
                        signal, needed_amount, score_threshold=20
                    )
                else:
                    # 太低分（<55）：不触发挪仓
                    logger.warning(
                        f"  ⛔ 信号评分{score}分 < 55分，不触发挪仓\n"
                        f"     说明: 评分过低，不值得卖出现有持仓"
                    )

                # 如果尝试了挪仓
                if rotation_attempted:
                    if rotation_success and freed_amount > 0:
                        logger.success(f"  ✅ 挪仓成功，释放资金 ${freed_amount:,.2f}")

                        # 发送挪仓通知
                        if rotation_details:
                            await self._notify_rotation_result(
                                new_signal=signal,
                                needed_amount=needed_amount,
                                freed_amount=freed_amount,
                                sold_positions=rotation_details,
                                success=True
                            )

                        # 重新获取账户信息并估算可买数量
                        logger.info("  🔄 挪仓后重新估算可买数量...")
                        try:
                            account = await self.trade_client.get_account()
                            available_cash = account["cash"].get(currency, 0)

                            # 重新估算
                            estimated_quantity = await self._estimate_available_quantity(
                                symbol=symbol,
                                price=current_price,
                                lot_size=lot_size,
                                currency=currency
                            )

                            if estimated_quantity > 0:
                                quantity = estimated_quantity
                                num_lots = quantity // lot_size
                                required_cash = current_price * quantity
                                dynamic_budget = required_cash

                                logger.success(
                                    f"  ✅ 挪仓后可买数量: {quantity}股 ({num_lots}手)，"
                                    f"需要 ${required_cash:.2f}"
                                )
                            else:
                                logger.warning(
                                    f"  ⚠️ 挪仓后预估数量仍为0\n"
                                    f"     可能原因: 购买力限制（非现金问题）"
                                )
                                if self.slack:
                                    await self._send_capacity_notification(
                                        symbol=symbol,
                                        signal=signal,
                                        price=current_price,
                                        available_cash=available_cash,
                                        buy_power=account.get('buy_power', {}).get(currency, 0),
                                        reason="挪仓后预估数量仍为0（购买力限制）"
                                    )
                                return
                        except Exception as e:
                            logger.error(f"  ❌ 挪仓后重新估算失败: {e}")
                            return
                    else:
                        logger.warning(f"  ⚠️ 挪仓未能释放足够资金")
                        if self.slack:
                            await self._send_capacity_notification(
                                symbol=symbol,
                                signal=signal,
                                price=current_price,
                                available_cash=available_cash,
                                buy_power=account.get('buy_power', {}).get(currency, 0),
                                reason="挪仓失败，预估数量为0"
                            )
                        return
                else:
                    # 未尝试挪仓（评分太低）
                    if self.slack:
                        await self._send_capacity_notification(
                            symbol=symbol,
                            signal=signal,
                            price=current_price,
                            available_cash=available_cash,
                            buy_power=account.get('buy_power', {}).get(currency, 0),
                            reason=f"预估数量为0且评分{score}分太低不触发挪仓"
                        )
                    return
            if not (quantity > 0 and dynamic_budget >= min_required_cash):
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

                # 🔥 分层挪仓策略：根据信号评分决定是否尝试挪仓
                if needed_amount > 0:
                    logger.info(
                        f"  📊 挪仓决策分析:\n"
                        f"     • 信号评分: {score}分\n"
                        f"     • 所需金额: ${needed_amount:.2f}\n"
                        f"     • 可用资金: ${available_cash:.2f}"
                    )

                    if score >= 70:
                        # 高分信号（70+）：积极挪仓，评分差10分即可
                        logger.info(
                            f"  🔥 高分信号({score}分 ≥ 70)：积极挪仓（评分差≥10分）"
                        )
                        rotation_success, freed_amount, rotation_details = await self._try_smart_rotation(
                            signal, needed_amount, score_threshold=10
                        )
                    elif score >= 60:
                        # 中分信号（60-70）：适度挪仓，评分差15分
                        logger.info(
                            f"  ⚡ 中分信号({score}分 ∈ [60,70))：适度挪仓（评分差≥15分）"
                        )
                        rotation_success, freed_amount, rotation_details = await self._try_smart_rotation(
                            signal, needed_amount, score_threshold=15
                        )
                    elif score >= 55:
                        # 低分信号（55-60）：保守挪仓，评分差20分
                        logger.info(
                            f"  💡 低分信号({score}分 ∈ [55,60))：保守挪仓（评分差≥20分）"
                        )
                        rotation_success, freed_amount, rotation_details = await self._try_smart_rotation(
                            signal, needed_amount, score_threshold=20
                        )
                    else:
                        # 太低分（<55）：不触发挪仓
                        logger.warning(
                            f"  ⛔ 信号评分{score}分 < 55分，不触发挪仓\n"
                            f"     说明: 评分过低，不值得卖出现有持仓"
                        )
                        rotation_success = False
                        freed_amount = 0
                        rotation_details = []
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
                    rotation_details = []

                if rotation_details:
                    await self._notify_rotation_result(
                        new_signal=signal,
                        needed_amount=needed_amount,
                        freed_amount=freed_amount,
                        sold_positions=rotation_details,
                        success=rotation_success
                    )

                if rotation_success:
                    logger.success(f"  ✅ 智能轮换成功，已释放 ${freed_amount:,.2f}")

                    # 重新获取账户信息
                    try:
                        account = await self.trade_client.get_account()
                        available_cash = account["cash"].get(currency, 0)

                        if available_cash >= required_cash:
                            logger.success(f"  💰 轮换后可用资金: ${available_cash:,.2f}，继续执行订单")

                            # 重新计算动态预算和购买数量
                            dynamic_budget = self._calculate_dynamic_budget(account, signal)

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

                    # 如果券商估算可买数量为0，则不视为失败：不下单，仅发送信号/容量提示
                    if not execution_result.success:
                        err = (execution_result.error_message or "").strip()
                        if "可买数量为0" in err:
                            logger.warning("  ⏸️ 券商可买数量为0，跳过下单，仅发送信号通知")
                            if self.slack:
                                await self._send_capacity_notification(
                                    symbol=symbol,
                                    signal=signal,
                                    price=current_price,
                                    available_cash=available_cash,
                                    buy_power=account.get('buy_power', {}).get(currency, 0),
                                    reason="券商估算可买数量为0（仅发送信号）"
                                )
                            return
                        # 其他错误按原逻辑处理为失败
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

                            # === 动态计算TSL百分比（Chandelier 近似） ===
                            indicators = signal.get('indicators', {}) or {}
                            atr_val = None
                            try:
                                atr_val = float(indicators.get('atr')) if indicators.get('atr') else None
                            except Exception:
                                atr_val = None

                            trailing_pct_dyn = None
                            if atr_val and final_price > 0:
                                k = float(getattr(self.settings, 'soft_exit_chandelier_k', 3.0) or 3.0)
                                trailing_pct_dyn = max(0.01, min(0.20, (k * float(atr_val)) / float(final_price)))
                                logger.info(
                                    f"  🧮 动态TSL百分比: ATR={atr_val:.4f}, 价=${final_price:.2f}, k={k} → {trailing_pct_dyn*100:.2f}%"
                                )

                            if stop_loss and stop_loss > 0:
                                # 🔥 智能选择：跟踪止损 vs 固定止损
                                if self.settings.backup_orders.use_trailing_stop:
                                    # 使用跟踪止损（TSLPPCT）- 自动跟随价格上涨锁定利润
                                    # 🔥 修复：side应该是"BUY"表示保护多头仓位，而非"SELL"
                                    stop_result = await self.trade_client.submit_trailing_stop(
                                        symbol=symbol,
                                        side="BUY",  # 修复：保护多头仓位（买入后持有）
                                        quantity=final_quantity,
                                        trailing_percent=(trailing_pct_dyn if trailing_pct_dyn is not None else self.settings.backup_orders.trailing_stop_percent),
                                        limit_offset=self.settings.backup_orders.trailing_stop_limit_offset,
                                        expire_days=self.settings.backup_orders.trailing_stop_expire_days,
                                        remark=(
                                            f"Trailing Stop {trailing_pct_dyn*100:.1f}% (ATR)" if trailing_pct_dyn is not None
                                            else f"Trailing Stop {self.settings.backup_orders.trailing_stop_percent*100:.1f}%"
                                        )
                                    )
                                    backup_stop_order_id = stop_result.get('order_id')
                                    logger.success(
                                        f"  ✅ 跟踪止损备份单已提交: {backup_stop_order_id} "
                                        f"(跟踪{(trailing_pct_dyn if trailing_pct_dyn is not None else self.settings.backup_orders.trailing_stop_percent)*100:.1f}%)"
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

            # 发送失败通知到 Slack
            if self.slack:
                await self._send_failure_notification(
                    symbol=symbol,
                    signal=signal,
                    error=str(e)
                )

            raise

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
                # 去杠杆：中等紧急度（限价单）
                urgency_level = 5
                execution_strategy = ExecutionStrategy.ADAPTIVE
                logger.info(f"  📊 去杠杆卖单：使用中等紧急度(urgency={urgency_level})，避免市价单风险")
            else:
                # 止损/止盈：高紧急度（市场开盘时可以使用市价单）
                urgency_level = 8
                execution_strategy = ExecutionStrategy.ADAPTIVE

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

    def _calculate_dynamic_budget(self, account: Dict, signal: Dict) -> float:
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
        net_assets = account.get("net_assets", {}).get(currency, 0)
        if net_assets <= 0:
            net_assets = 50000  # 默认值

        # 基础预算（总资产的百分比）
        base_budget = net_assets * self.min_position_size_pct

        # 根据评分调整预算（更激进的高分信号仓位）
        if score >= 80:
            # 极强买入信号：重仓（30-40%）
            budget_pct = 0.30 + (score - 80) / 200  # 80分=30%, 100分=40%
        elif score >= 60:
            # 强买入信号：标准仓（20-30%）
            budget_pct = 0.20 + (score - 60) / 200  # 60分=20%, 80分=30%
        elif score >= 45:
            # 买入信号：试探性小仓位（5-12%）
            budget_pct = 0.05 + (score - 45) / 200  # 45分=5%, 59分=12%
        else:
            # 低于45分：不应该生成信号（WEAK_BUY已禁用）
            budget_pct = 0.05  # 兜底最小值

        # 限制在合理范围内
        budget_pct = max(self.min_position_size_pct, min(budget_pct, self.max_position_size_pct))

        dynamic_budget = net_assets * budget_pct

        # 🔥 不能超过该币种的实际购买力和融资额度
        available_cash = account.get("cash", {}).get(currency, 0)
        remaining_finance = account.get("remaining_finance", {}).get(currency, 0)
        buy_power = account.get("buy_power", {}).get(currency, 0)

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
        currency: str
    ) -> int:
        """
        调用交易端口预估最大可买数量（含融资），并按手数取整。

        Returns:
            int: 按手数取整后的最大可买数量，若不可用返回0
        """
        try:
            estimate = await self.trade_client.estimate_max_purchase_quantity(
                symbol=symbol,
                order_type=openapi.OrderType.Limit,
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

    async def _send_failure_notification(
        self,
        symbol: str,
        signal: Dict,
        error: str
    ):
        """发送订单执行失败通知到Slack"""
        try:
            signal_type = signal.get('type', 'BUY')
            score = signal.get('score', 0)
            price = signal.get('price', 0)

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
                            cash = account["cash"].get(currency, 0)
                            power = account.get("buy_power", {}).get(currency, 0)
                        except:
                            currency = "HKD" if ".HK" in symbol else "USD"
                            cash = power = 0

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
                    hkd_cash = account["cash"].get("HKD", 0)
                    usd_cash = account["cash"].get("USD", 0)
                    hkd_power = account.get("buy_power", {}).get("HKD", 0)
                    usd_power = account.get("buy_power", {}).get("USD", 0)
                except:
                    hkd_cash = usd_cash = hkd_power = usd_power = 0

                # 构建延迟信号列表
                signals_list = []
                for sig in remaining_signals[:5]:  # 最多显示5个
                    symbol = sig.get('symbol', 'N/A')
                    score = sig.get('score', 0)
                    retry_count = sig.get('retry_count', 0)
                    delay_min = min(self.settings.funds_retry_delay * retry_count, 30)
                    signals_list.append(f"   • {symbol} (评分{score}, {delay_min}分钟后重试)")

                more_count = len(remaining_signals) - 5
                if more_count > 0:
                    signals_list.append(f"   • ... 还有{more_count}个信号")

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
