#!/usr/bin/env python3
"""调试为什么强信号不触发下单"""

import asyncio
from datetime import datetime
from loguru import logger
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from src.longport_quant.config import get_settings
from src.longport_quant.execution.client import LongportTradingClient
from src.longport_quant.persistence.order_manager import OrderManager


async def check_blocking_conditions():
    """检查所有可能阻止下单的条件"""

    settings = get_settings()
    order_manager = OrderManager()

    logger.info("="*70)
    logger.info("检查下单阻塞条件")
    logger.info("="*70)

    async with LongportTradingClient(settings) as trade_client:
        # 1. 检查账户状态
        logger.info("\n1. 账户状态检查")
        logger.info("-"*40)

        try:
            balances = await trade_client.account_balance()
            positions_resp = await trade_client.stock_positions()

            # 显示现金情况
            for balance in balances:
                currency = balance.currency
                buy_power = float(balance.buy_power) if hasattr(balance, 'buy_power') else 0
                logger.info(f"  {currency} 购买力: ${buy_power:,.2f}")

            # 统计持仓
            position_count = 0
            positions = {}
            for channel in positions_resp.channels:
                for pos in channel.positions:
                    position_count += 1
                    positions[pos.symbol] = {
                        "quantity": pos.quantity,
                        "cost": float(pos.cost_price) if pos.cost_price else 0
                    }

            logger.info(f"  当前持仓数: {position_count}/10")

            if position_count >= 10:
                logger.warning("  ⚠️ 已达到最大持仓数，无法新开仓！")
            else:
                logger.success(f"  ✅ 还可以开 {10-position_count} 个新仓位")

            # 显示当前持仓
            if positions:
                logger.info("\n  当前持仓:")
                for symbol, pos in positions.items():
                    logger.info(f"    {symbol}: {pos['quantity']}股 @ ${pos['cost']:.2f}")

        except Exception as e:
            logger.error(f"  获取账户信息失败: {e}")

        # 2. 检查今日订单
        logger.info("\n2. 今日订单检查")
        logger.info("-"*40)

        pending_buy_orders = {}
        symbol_trade_count = {}

        try:
            # 同步券商订单
            sync_result = await order_manager.sync_with_broker(trade_client)
            logger.info(f"  同步结果: 新增{sync_result['added']}个, 更新{sync_result['updated']}个")

            # 获取所有今日订单
            all_orders = await order_manager.get_all_today_orders()

            # 统计各种状态
            order_stats = {}
            symbol_trade_count = {}
            pending_buy_orders = {}

            for order in all_orders:
                # 统计状态
                status = order.status
                order_stats[status] = order_stats.get(status, 0) + 1

                # 统计每个标的的交易次数
                if order.status in ["Filled", "PartialFilled"]:
                    symbol_trade_count[order.symbol] = symbol_trade_count.get(order.symbol, 0) + 1

                # 找出未完成的买单
                if order.side == "BUY" and order.status in ["New", "WaitToNew", "PartialFilled"]:
                    pending_buy_orders[order.symbol] = order.status

            logger.info(f"  今日订单总数: {len(all_orders)}")
            logger.info(f"  订单状态分布: {order_stats}")

            # 显示每个标的的交易次数
            if symbol_trade_count:
                logger.info("\n  各标的今日交易次数:")
                for symbol, count in symbol_trade_count.items():
                    if count >= 2:
                        logger.warning(f"    {symbol}: {count}次 (已达上限!)")
                    else:
                        logger.info(f"    {symbol}: {count}次")

            # 显示未完成的买单
            if pending_buy_orders:
                logger.warning("\n  ⚠️ 发现未完成的买单:")
                for symbol, status in pending_buy_orders.items():
                    logger.warning(f"    {symbol}: {status}")
            else:
                logger.success("  ✅ 没有未完成的买单")

        except Exception as e:
            logger.error(f"  检查订单失败: {e}")

        # 3. 诊断建议
        logger.info("\n3. 诊断结果")
        logger.info("-"*40)

        blocking_reasons = []

        # 检查是否满仓
        if position_count >= 10:
            blocking_reasons.append("已达最大持仓数(10)，需要卖出部分持仓才能买入新标的")

        # 检查是否有未完成买单
        if pending_buy_orders:
            blocking_reasons.append(f"有{len(pending_buy_orders)}个未完成的买单，需要等待成交或取消")

        # 检查是否有标的达到交易次数上限
        over_limit_symbols = [s for s, c in symbol_trade_count.items() if c >= 2]
        if over_limit_symbols:
            blocking_reasons.append(f"以下标的已达今日交易上限: {', '.join(over_limit_symbols)}")

        if blocking_reasons:
            logger.warning("\n  🚫 下单被阻塞的原因:")
            for i, reason in enumerate(blocking_reasons, 1):
                logger.warning(f"    {i}. {reason}")
        else:
            logger.success("\n  ✅ 没有发现阻塞下单的条件")
            logger.info("    如果仍无法下单，请检查:")
            logger.info("    1. 资金是否充足")
            logger.info("    2. 标的是否已持有")
            logger.info("    3. 信号强度是否达到阈值")


async def main():
    await check_blocking_conditions()

    logger.info("\n" + "="*70)
    logger.info("调试建议:")
    logger.info("1. 运行脚本时添加 LOGURU_LEVEL=DEBUG 环境变量查看更多细节")
    logger.info("2. 检查 advanced_technical_trading.py 的日志输出")
    logger.info("3. 确认WebSocket连接是否成功")
    logger.info("="*70)


if __name__ == "__main__":
    asyncio.run(main())