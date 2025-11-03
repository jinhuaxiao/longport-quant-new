#!/usr/bin/env python3
"""测试批量取消订单功能"""

import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from loguru import logger
from longport_quant.config import get_settings
from longport_quant.execution.client import LongportTradingClient
from longport_quant.persistence.order_manager import OrderManager


async def test_batch_cancel():
    """测试批量取消功能"""
    logger.info("=== 测试批量取消订单功能 ===\n")

    settings = get_settings()
    order_manager = OrderManager()

    async with LongportTradingClient(settings) as client:
        # 测试1: 查询历史订单
        logger.info("测试1: 查询历史订单")
        try:
            from datetime import datetime, timedelta
            old_orders = await order_manager.get_old_orders(keep_days=1)
            logger.info(f"✅ 查询到 {len(old_orders)} 个历史订单（数据库）")
            for order in old_orders[:5]:  # 只显示前5个
                logger.info(f"  - {order.symbol} | {order.side} | {order.status} | {order.created_at}")
        except Exception as e:
            logger.error(f"❌ 查询历史订单失败: {e}")

        print()

        # 测试2: 查询券商历史订单（预览模式）
        logger.info("测试2: 批量取消历史订单（预览模式）")
        try:
            result = await order_manager.cancel_old_orders(
                trade_client=client,
                keep_days=1,
                dry_run=True  # 预览模式
            )

            logger.info(f"✅ 预览结果:")
            logger.info(f"  - 查询到的订单数: {result['total_found']}")
            logger.info(f"  - 可取消订单数:   {result['cancelable']}")
            logger.info(f"  - 预览模式:       {result['dry_run']}")

            if result['orders']:
                logger.info(f"\n  订单详情（前5个）:")
                for order in result['orders'][:5]:
                    logger.info(
                        f"    • {order['symbol']:10s} | {order['side']:4s} | "
                        f"{order['status']:15s} | {order['order_id'][:16]}..."
                    )
        except Exception as e:
            logger.error(f"❌ 预览取消失败: {e}")
            import traceback
            traceback.print_exc()

        print()

        # 测试3: 测试批量取消API（使用空列表，不实际取消）
        logger.info("测试3: 测试批量取消API（空列表）")
        try:
            result = await client.cancel_orders_batch([])
            logger.info(f"✅ 批量取消API正常:")
            logger.info(f"  - 总数: {result['total']}")
            logger.info(f"  - 成功: {result['succeeded']}")
            logger.info(f"  - 失败: {result['failed']}")
        except Exception as e:
            logger.error(f"❌ 批量取消API测试失败: {e}")

        print()
        logger.info("=== 所有测试完成 ===")


if __name__ == "__main__":
    asyncio.run(test_batch_cancel())
