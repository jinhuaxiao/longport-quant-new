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
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.messaging import SignalQueue
from longport_quant.notifications.slack import SlackNotifier
from longport_quant.utils import LotSizeHelper
from longport_quant.persistence.order_manager import OrderManager
from longport_quant.persistence.stop_manager import StopLossManager


class InsufficientFundsError(Exception):
    """资金不足异常"""
    pass


class OrderExecutor:
    """订单执行器（从队列消费信号并执行）"""

    def __init__(self):
        """初始化订单执行器"""
        self.settings = get_settings()
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
        self.max_position_size_pct = 0.30  # 最大仓位30%
        self.min_cash_reserve = 1000  # 最低现金储备
        self.use_adaptive_budget = True  # 启用自适应预算

        # 组件（延迟初始化）
        self.trade_client = None
        self.quote_client = None
        self.slack = None
        self.lot_size_helper = LotSizeHelper()
        self.order_manager = OrderManager()
        self.stop_manager = StopLossManager()

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
                logger.info("")

                while True:
                    try:
                        # 从队列消费信号（阻塞等待）
                        signal = await self.signal_queue.consume_signal()

                        if not signal:
                            # 队列为空，短暂等待
                            await asyncio.sleep(1)
                            continue

                        symbol = signal.get('symbol')
                        signal_type = signal.get('type')
                        score = signal.get('score', 0)

                        logger.info(f"\n{'='*70}")
                        logger.info(f"📥 收到信号: {symbol}, 类型={signal_type}, 评分={score}")
                        logger.info(f"{'='*70}")

                        # 执行订单（带超时保护）
                        try:
                            # 60秒超时保护
                            await asyncio.wait_for(
                                self.execute_order(signal),
                                timeout=60.0
                            )

                            # 标记信号处理完成
                            await self.signal_queue.mark_signal_completed(signal)

                        except asyncio.TimeoutError:
                            error_msg = "订单执行超时（60秒）"
                            logger.error(f"❌ {error_msg}: {symbol}")

                            # 标记信号失败（会自动重试）
                            await self.signal_queue.mark_signal_failed(
                                signal,
                                error_message=error_msg,
                                retry=True
                            )

                        except InsufficientFundsError as e:
                            # 资金不足：直接标记为完成，不重试
                            # （避免资金不足的信号反复重试浪费资源）
                            logger.info(f"  ℹ️ {symbol}: 资金不足，跳过此信号")
                            await self.signal_queue.mark_signal_completed(signal)

                        except Exception as e:
                            error_msg = f"{type(e).__name__}: {str(e)}"
                            logger.error(f"❌ 执行订单失败: {error_msg}")

                            # 标记信号失败（会自动重试）
                            await self.signal_queue.mark_signal_failed(
                                signal,
                                error_message=error_msg,
                                retry=True
                            )

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

        if quantity <= 0:
            logger.warning(
                f"  ⚠️ {symbol}: 动态预算不足以购买1手 "
                f"(手数: {lot_size}, 需要: ${lot_size * current_price:.2f}, "
                f"动态预算: ${dynamic_budget:.2f})"
            )
            raise InsufficientFundsError(f"动态预算不足（需要${lot_size * current_price:.2f}，预算${dynamic_budget:.2f}）")

        num_lots = quantity // lot_size
        required_cash = current_price * quantity

        # 7. 资金充足性检查（带智能轮换）
        if required_cash > available_cash:
            logger.warning(
                f"  ⚠️ {symbol}: 资金不足 "
                f"(需要 ${required_cash:.2f}, 可用 ${available_cash:.2f})"
            )

            # 尝试智能持仓轮换释放资金
            needed_amount = required_cash - available_cash
            logger.info(f"  🔄 尝试智能持仓轮换释放 ${needed_amount:,.2f}...")

            rotation_success, freed_amount = await self._try_smart_rotation(
                signal, needed_amount
            )

            if rotation_success:
                logger.success(f"  ✅ 智能轮换成功，已释放 ${freed_amount:,.2f}")

                # 重新获取账户信息
                try:
                    account = await self.trade_client.get_account()
                    available_cash = account["cash"].get(currency, 0)

                    if available_cash >= required_cash:
                        logger.success(f"  💰 轮换后可用资金: ${available_cash:,.2f}，继续执行订单")
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

        # 10. 提交订单
        try:
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
                f"   评分: {score}/100\n"
                f"   数量: {quantity}股 ({num_lots}手 × {lot_size}股/手)\n"
                f"   下单价: ${order_price:.2f}\n"
                f"   总额: ${order_price * quantity:.2f}\n"
                f"   止损位: ${signal.get('stop_loss', 0):.2f}\n"
                f"   止盈位: ${signal.get('take_profit', 0):.2f}"
            )

            # 11. 记录止损止盈
            self.positions_with_stops[symbol] = {
                "entry_price": current_price,
                "stop_loss": signal.get('stop_loss'),
                "take_profit": signal.get('take_profit'),
                "atr": signal.get('indicators', {}).get('atr'),
            }

            # 保存到数据库
            try:
                await self.stop_manager.set_position_stops(
                    account_id=account.get("account_id", ""),
                    symbol=symbol,
                    stop_loss=signal.get('stop_loss'),
                    take_profit=signal.get('take_profit')
                )
            except Exception as e:
                logger.warning(f"⚠️ 保存止损止盈失败: {e}")

            # 12. 发送Slack通知
            if self.slack:
                await self._send_buy_notification(symbol, signal, order, quantity, order_price, required_cash)

        except Exception as e:
            logger.error(f"❌ 提交订单失败: {e}")
            raise

    async def _execute_sell_order(self, signal: Dict):
        """执行卖出订单（止损/止盈）"""
        symbol = signal['symbol']
        signal_type = signal.get('type', 'SELL')
        quantity = signal.get('quantity', 0)
        current_price = signal.get('price', 0)
        reason = signal.get('reason', '平仓')

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

        # 提交订单
        try:
            order = await self.trade_client.submit_order({
                "symbol": symbol,
                "side": "SELL",
                "quantity": quantity,
                "price": order_price
            })

            logger.success(
                f"\n✅ 平仓订单已提交: {order['order_id']}\n"
                f"   标的: {symbol}\n"
                f"   原因: {reason}\n"
                f"   数量: {quantity}股\n"
                f"   价格: ${order_price:.2f}\n"
                f"   总额: ${order_price * quantity:.2f}"
            )

            # 清除止损止盈记录
            if symbol in self.positions_with_stops:
                del self.positions_with_stops[symbol]

            # 发送Slack通知
            if self.slack:
                await self._send_sell_notification(symbol, signal, order, quantity, order_price)

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

        # 根据评分调整预算
        if score >= 60:
            # 强买入信号：分配更多（20-30%）
            budget_pct = 0.20 + (score - 60) / 400  # 60分=20%, 100分=30%
        elif score >= 45:
            # 买入信号：中等（10-20%）
            budget_pct = 0.10 + (score - 45) / 150  # 45分=10%, 60分=20%
        else:
            # 弱买入信号：较少（5-10%）
            budget_pct = 0.05 + (score - 30) / 300  # 30分=5%, 45分=10%

        # 限制在合理范围内
        budget_pct = max(self.min_position_size_pct, min(budget_pct, self.max_position_size_pct))

        dynamic_budget = net_assets * budget_pct

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
        """发送卖出通知到Slack"""
        try:
            signal_type = signal.get('type', 'SELL')
            reason = signal.get('reason', '平仓')

            emoji = "🛑" if "止损" in reason else ("🎯" if "止盈" in reason else "💵")

            message = (
                f"{emoji} *平仓订单已提交*\n\n"
                f"📋 订单ID: `{order.get('order_id', 'N/A')}`\n"
                f"📊 标的: *{symbol}*\n"
                f"💡 原因: {reason}\n\n"
                f"💰 *交易信息*:\n"
                f"   • 数量: {quantity}股\n"
                f"   • 价格: ${order_price:.2f}\n"
                f"   • 总额: ${order_price * quantity:.2f}\n"
            )

            await self.slack.send(message)

        except Exception as e:
            logger.warning(f"⚠️ 发送Slack通知失败: {e}")

    async def _try_smart_rotation(
        self,
        signal: Dict,
        needed_amount: float
    ) -> tuple[bool, float]:
        """
        尝试通过智能持仓轮换释放资金

        Args:
            signal: 新信号数据（包含symbol, score等）
            needed_amount: 需要释放的资金量

        Returns:
            (成功与否, 实际释放的资金量)
        """
        try:
            # 动态导入SmartPositionRotator
            import sys
            from pathlib import Path
            sys.path.append(str(Path(__file__).parent))

            from smart_position_rotation import SmartPositionRotator

            rotator = SmartPositionRotator()

            # 调用智能轮换释放资金
            success, freed = await rotator.try_free_up_funds(
                needed_amount=needed_amount,
                new_signal=signal,
                trade_client=self.trade_client,
                quote_client=self.quote_client,
                score_threshold=10  # 新信号需高出10分才替换
            )

            return success, freed

        except ImportError as e:
            logger.error(f"❌ 导入SmartPositionRotator失败: {e}")
            logger.warning("⚠️ 智能轮换功能不可用，跳过轮换尝试")
            return False, 0.0
        except Exception as e:
            logger.error(f"❌ 智能轮换执行失败: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False, 0.0


async def main():
    """主函数"""
    executor = OrderExecutor()

    try:
        await executor.run()
    except Exception as e:
        logger.error(f"❌ 订单执行器运行失败: {e}")
        import traceback
        logger.error(traceback.format_exc())


if __name__ == "__main__":
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
    asyncio.run(main())
