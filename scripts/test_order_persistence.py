#!/usr/bin/env python3
"""测试订单数据库持久化功能"""

import asyncio
from datetime import datetime, timedelta
from loguru import logger

from longport_quant.config import get_settings
from longport_quant.execution.client import LongportTradingClient
from longport_quant.persistence.order_manager import OrderManager


async def test_order_persistence():
    """测试订单持久化功能"""

    logger.info("=" * 70)
    logger.info("测试订单数据库持久化功能")
    logger.info("=" * 70)

    settings = get_settings()
    order_manager = OrderManager()

    # 1. 测试保存订单
    logger.info("\n1. 测试保存订单到数据库")
    test_orders = [
        {
            "order_id": "TEST_001",
            "symbol": "0700.HK",
            "side": "BUY",
            "quantity": 100,
            "price": 350.0,
            "status": "New"
        },
        {
            "order_id": "TEST_002",
            "symbol": "9988.HK",
            "side": "BUY",
            "quantity": 200,
            "price": 85.0,
            "status": "Filled"
        },
        {
            "order_id": "TEST_003",
            "symbol": "1929.HK",
            "side": "BUY",
            "quantity": 1000,
            "price": 11.5,
            "status": "New"
        }
    ]

    for order_data in test_orders:
        order = await order_manager.save_order(**order_data)
        logger.info(f"  ✅ 已保存订单: {order.order_id} - {order.symbol} {order.side} {order.quantity}@{order.price}")

    # 2. 测试查询今日订单
    logger.info("\n2. 测试查询今日订单")
    today_orders = await order_manager.get_today_orders()
    logger.info(f"  📊 今日订单总数: {len(today_orders)}")
    for order in today_orders:
        logger.info(f"    • {order.symbol}: {order.side} {order.quantity}股 @ ${order.price:.2f} (状态: {order.status})")

    # 3. 测试查询特定标的订单
    logger.info("\n3. 测试查询特定标的订单")
    symbol = "0700.HK"
    symbol_orders = await order_manager.get_today_orders(symbol)
    logger.info(f"  📊 {symbol} 今日订单: {len(symbol_orders)}个")

    # 4. 测试检查是否有今日订单
    logger.info("\n4. 测试检查今日订单存在性")
    for symbol in ["0700.HK", "9988.HK", "1929.HK", "0001.HK"]:
        has_order = await order_manager.has_today_order(symbol, "BUY")
        status = "有买单" if has_order else "无买单"
        logger.info(f"  {symbol}: {status}")

    # 5. 测试获取今日买入标的列表
    logger.info("\n5. 测试获取今日买入标的列表")
    buy_symbols = await order_manager.get_today_buy_symbols()
    logger.info(f"  📊 今日买入标的: {len(buy_symbols)}个")
    if buy_symbols:
        logger.info(f"  标的列表: {', '.join(sorted(buy_symbols))}")

    # 6. 测试更新订单状态
    logger.info("\n6. 测试更新订单状态")
    success = await order_manager.update_order_status("TEST_001", "Filled")
    if success:
        logger.info("  ✅ 成功更新订单 TEST_001 状态为 Filled")
    else:
        logger.info("  ❌ 更新订单状态失败")

    # 验证更新结果
    updated_orders = await order_manager.get_today_orders("0700.HK")
    for order in updated_orders:
        if order.order_id == "TEST_001":
            logger.info(f"  验证: TEST_001 当前状态为 {order.status}")

    # 7. 测试与券商同步（需要真实交易客户端）
    logger.info("\n7. 测试与券商同步")
    try:
        async with LongportTradingClient(settings) as trade_client:
            sync_result = await order_manager.sync_with_broker(trade_client)
            logger.info(f"  📊 同步结果:")
            logger.info(f"    • 已成交: {len(sync_result['executed'])} 个")
            if sync_result['executed']:
                logger.info(f"      {', '.join(sync_result['executed'][:5])}")
            logger.info(f"    • 待成交: {len(sync_result['pending'])} 个")
            if sync_result['pending']:
                logger.info(f"      {', '.join(sync_result['pending'][:5])}")
    except Exception as e:
        logger.warning(f"  ⚠️ 同步失败（可能没有真实订单）: {e}")

    # 8. 测试清理旧订单（创建一个旧订单用于测试）
    logger.info("\n8. 测试清理旧订单")

    # 创建一个8天前的订单
    old_date = datetime.now() - timedelta(days=8)
    old_order = await order_manager.save_order(
        order_id="OLD_TEST_001",
        symbol="TEST.HK",
        side="BUY",
        quantity=100,
        price=10.0,
        status="Expired",
        created_at=old_date
    )
    logger.info(f"  创建测试旧订单: {old_order.order_id} (创建于8天前)")

    # 清理7天前的订单
    await order_manager.cleanup_old_orders(days=7)
    logger.info("  ✅ 已执行清理7天前订单")

    # 验证旧订单已被清理
    all_orders = await order_manager.get_today_orders()
    old_order_exists = any(o.order_id == "OLD_TEST_001" for o in all_orders)
    if not old_order_exists:
        logger.info("  ✅ 旧订单已被成功清理")
    else:
        logger.info("  ❌ 旧订单清理失败")

    logger.info("\n" + "=" * 70)
    logger.info("订单持久化功能测试完成")
    logger.info("=" * 70)

    # 显示最终数据库状态
    logger.info("\n📁 最终数据库状态:")
    final_orders = await order_manager.get_today_orders()
    logger.info(f"  今日订单总数: {len(final_orders)}")

    buy_count = sum(1 for o in final_orders if o.side == "BUY")
    sell_count = sum(1 for o in final_orders if o.side == "SELL")
    filled_count = sum(1 for o in final_orders if o.status == "Filled")
    pending_count = sum(1 for o in final_orders if o.status in ["New", "WaitToNew"])

    logger.info(f"  买单: {buy_count} | 卖单: {sell_count}")
    logger.info(f"  已成交: {filled_count} | 待成交: {pending_count}")


async def main():
    """主函数"""
    try:
        await test_order_persistence()
    except KeyboardInterrupt:
        logger.info("\n测试中断")
    except Exception as e:
        logger.error(f"测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════════════╗
║                订单数据库持久化功能测试                                 ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  测试内容:                                                            ║
║    1. 保存订单到数据库                                                 ║
║    2. 查询今日订单                                                    ║
║    3. 查询特定标的订单                                                 ║
║    4. 检查今日订单存在性                                               ║
║    5. 获取今日买入标的列表                                              ║
║    6. 更新订单状态                                                    ║
║    7. 与券商同步订单                                                  ║
║    8. 清理旧订单                                                      ║
║                                                                       ║
║  这个测试将验证订单持久化功能是否正常工作                                ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
    """)

    asyncio.run(main())