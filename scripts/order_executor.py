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
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from pathlib import Path
from loguru import logger
from typing import Dict, Optional

# 添加项目根目录到路径
sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings
from longport_quant.execution.client import LongportTradingClient
from longport_quant.execution.smart_router import SmartOrderRouter, OrderRequest, ExecutionStrategy
from longport_quant.execution.risk_assessor import RiskAssessor
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.messaging import SignalQueue
from longport_quant.notifications.slack import SlackNotifier
from longport_quant.utils import LotSizeHelper
from longport_quant.persistence.order_manager import OrderManager
from longport_quant.persistence.stop_manager import StopLossManager
from longport_quant.persistence.position_manager import RedisPositionManager
from longport_quant.persistence.db import DatabaseSessionManager


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

                # 初始化Slack（可选）
                if self.settings.slack_webhook_url:
                    self.slack = SlackNotifier(str(self.settings.slack_webhook_url))
                    logger.info(f"✅ Slack通知已初始化: {str(self.settings.slack_webhook_url)[:50]}...")
                else:
                    logger.warning("⚠️ 未配置SLACK_WEBHOOK_URL，Slack通知已禁用")

                # 🔥 连接Redis持仓管理器
                await self.position_manager.connect()
                logger.info("✅ Redis持仓管理器已连接")

                # 🔥 初始化SmartOrderRouter（用于TWAP/VWAP算法订单）
                db_manager = DatabaseSessionManager(self.settings.database_dsn, auto_init=True)
                trade_ctx = await trade_client.get_trade_context()
                self.smart_router = SmartOrderRouter(trade_ctx, db_manager)
                logger.info("✅ SmartOrderRouter已初始化（支持TWAP/VWAP算法订单）")

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
                                # 资金不足：停止处理当前批次，剩余信号延迟重试
                                logger.warning(f"  ⚠️ [{idx}/{len(batch)}] {symbol}: 资金不足")
                                logger.info(f"  💡 策略：将剩余{len(batch)-idx}个信号延迟重试")

                                # 当前信号也加入待重新入队列表
                                remaining_signals.append(signal)

                                # 将后续所有信号也加入待重新入队列表
                                remaining_signals.extend(batch[idx:])

                                funds_exhausted = True
                                break  # 跳出循环，不再处理本批次剩余信号

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
                        if funds_exhausted:
                            logger.warning(f"⚠️ 批次处理中断: 资金不足")
                            logger.info(f"  已处理: {idx-1}/{len(batch)}个信号")
                            logger.info(f"  待重试: {len(remaining_signals)}个信号")

                            # 重新入队剩余信号
                            requeued = await self._requeue_remaining(
                                remaining_signals,
                                reason="资金不足"
                            )
                            logger.info(f"  ✅ 已重新入队: {requeued}个信号")
                        else:
                            logger.success(f"✅ 批次处理完成: {len(batch)}/{len(batch)}个信号")

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

                rotation_success, freed_amount, rotation_details = await self._try_smart_rotation(
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

        # 提交订单（使用SmartOrderRouter的自适应策略）
        try:
            # 创建订单请求
            # 止损/止盈订单使用高紧急度（自动选择AGGRESSIVE策略）
            order_request = OrderRequest(
                symbol=symbol,
                side="SELL",
                quantity=quantity,
                order_type="LIMIT",
                limit_price=order_price,
                strategy=ExecutionStrategy.ADAPTIVE,  # 自适应策略
                urgency=8,  # 高紧急度（止损/止盈需要快速执行）
                max_slippage=0.015,  # 允许1.5%滑点
                signal=signal,
                metadata={
                    "reason": reason,
                    "signal_type": signal_type
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

        # 如果账户使用融资（available_cash为负），检查剩余融资额度
        if available_cash < 0:
            # 使用融资账户，限制不超过剩余融资额度
            if remaining_finance > 0 and dynamic_budget > remaining_finance:
                logger.warning(
                    f"  ⚠️ 动态预算${dynamic_budget:,.2f}超出剩余融资额度${remaining_finance:,.2f}，"
                    f"调整为剩余额度"
                )
                dynamic_budget = remaining_finance
            elif remaining_finance <= 1000:
                # 融资额度不足，严重警告
                logger.error(
                    f"  ❌ 剩余融资额度不足: ${remaining_finance:,.2f}，"
                    f"无法下单（需要${dynamic_budget:,.2f}）"
                )
                raise InsufficientFundsError(f"融资额度不足: 剩余${remaining_finance:,.2f}")
        else:
            # 普通账户，不能超过可用现金
            if dynamic_budget > available_cash:
                # 🔥 关键修复：可用资金不足时，按评分比例基于可用资金重新计算
                # 而不是直接用全部可用资金（这样会导致低分信号用过大仓位）
                adjusted_budget = available_cash * budget_pct
                logger.warning(
                    f"  ⚠️ 动态预算${dynamic_budget:,.2f}超出{currency}可用资金${available_cash:,.2f}\n"
                    f"     按评分比例({budget_pct:.1%})重新计算: ${adjusted_budget:,.2f} "
                    f"(而非使用全部可用资金)"
                )
                dynamic_budget = adjusted_budget

        logger.debug(
            f"  动态预算计算: 评分={score}, 预算比例={budget_pct:.2%}, "
            f"金额=${dynamic_budget:.2f}"
        )

        return dynamic_budget

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

            emoji = "🛑" if "止损" in reason else ("🎯" if "止盈" in reason else "💵")

            # 基础信息
            message = (
                f"{emoji} *平仓订单已提交*\n\n"
                f"📋 订单ID: `{order.get('order_id', 'N/A')}`\n"
                f"📊 标的: *{symbol}*\n"
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

    async def _try_smart_rotation(
        self,
        signal: Dict,
        needed_amount: float
    ) -> tuple[bool, float, list[dict]]:
        """
        尝试通过智能持仓轮换释放资金

        Args:
            signal: 新信号数据（包含symbol, score等）
            needed_amount: 需要释放的资金量

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
                f"评分={signal.get('score', 0)}, 需要资金=${needed_amount:,.2f}"
            )

            success, freed, sold_positions = await rotator.try_free_up_funds(
                needed_amount=needed_amount,
                new_signal=signal,
                trade_client=self.trade_client,
                quote_client=self.quote_client,
                score_threshold=15  # 🔥 提高到15分，避免频繁买卖浪费手续费
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
                    # 🔥 队列为空，增加计数
                    consecutive_empty_attempts += 1

                    # 🔥 快速通道：第一次为空就立即退出
                    if batch_window == 0 and len(batch) == 0:
                        logger.debug(f"  ⚡ 快速通道：队列为空，立即返回")
                        break

                    if consecutive_empty_attempts >= max_empty_attempts:
                        # 🔥 连续多次为空，退出循环
                        logger.debug(
                            f"  💤 连续{consecutive_empty_attempts}次队列为空，"
                            f"可能只有延迟信号，结束批次收集"
                        )
                        break

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
                    if consecutive_empty_attempts >= max_empty_attempts:
                        logger.debug(
                            f"  💤 连续{consecutive_empty_attempts}次超时，"
                            f"结束批次收集"
                        )
                        break
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
            else:
                logger.error(f"  ❌ {symbol} 重新入队失败")

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
