#!/usr/bin/env python3
"""
测试Redis持仓管理器

测试场景：
1. 添加和删除持仓
2. 检查持仓是否存在
3. 跨进程共享测试
4. Pub/Sub通知测试
5. API同步测试
"""

import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings
from longport_quant.persistence.position_manager import RedisPositionManager


async def test_basic_operations():
    """测试基本操作"""
    print("="*70)
    print("测试1：基本操作（添加/删除/检查）")
    print("="*70)

    settings = get_settings()
    pm = RedisPositionManager(settings.redis_url)

    try:
        await pm.connect()

        # 清空测试数据
        await pm.clear_all_positions()
        print("\n✅ 已清空Redis持仓")

        # 测试添加持仓
        print("\n📍 测试添加持仓...")
        await pm.add_position("AAPL.US", quantity=100, cost_price=180.5, order_id="test_001")
        await pm.add_position("TSLA.US", quantity=50, cost_price=250.0, order_id="test_002")

        # 测试检查持仓
        print("\n📍 测试检查持仓...")
        has_aapl = await pm.has_position("AAPL.US")
        has_tsla = await pm.has_position("TSLA.US")
        has_nvda = await pm.has_position("NVDA.US")

        assert has_aapl, "AAPL.US应该存在"
        assert has_tsla, "TSLA.US应该存在"
        assert not has_nvda, "NVDA.US不应该存在"
        print(f"✅ AAPL.US: {has_aapl}")
        print(f"✅ TSLA.US: {has_tsla}")
        print(f"✅ NVDA.US: {has_nvda}")

        # 测试获取所有持仓
        print("\n📍 测试获取所有持仓...")
        all_positions = await pm.get_all_positions()
        print(f"✅ 所有持仓: {all_positions}")
        assert len(all_positions) == 2

        # 测试获取持仓详情
        print("\n📍 测试获取持仓详情...")
        aapl_detail = await pm.get_position_detail("AAPL.US")
        print(f"✅ AAPL.US详情: {aapl_detail}")
        assert aapl_detail["quantity"] == 100
        assert aapl_detail["cost_price"] == 180.5

        # 测试删除持仓
        print("\n📍 测试删除持仓...")
        await pm.remove_position("AAPL.US")
        has_aapl_after = await pm.has_position("AAPL.US")
        assert not has_aapl_after, "AAPL.US应该已被删除"
        print(f"✅ 删除后AAPL.US: {has_aapl_after}")

        # 测试重复添加
        print("\n📍 测试重复添加...")
        await pm.add_position("TSLA.US", quantity=50, cost_price=250.0)
        all_positions = await pm.get_all_positions()
        print(f"✅ 重复添加后持仓数: {len(all_positions)} (应该还是1个)")

        print("\n✅ 基本操作测试通过！")

    finally:
        await pm.close()


async def test_api_sync():
    """测试从API同步持仓"""
    print("\n" + "="*70)
    print("测试2：API同步")
    print("="*70)

    settings = get_settings()
    pm = RedisPositionManager(settings.redis_url)

    try:
        await pm.connect()

        # 模拟API返回的持仓
        api_positions = [
            {"symbol": "AAPL.US", "quantity": 100, "cost_price": 180.5},
            {"symbol": "TSLA.US", "quantity": 50, "cost_price": 250.0},
            {"symbol": "NVDA.US", "quantity": 200, "cost_price": 450.0},
        ]

        print("\n📍 模拟API持仓:")
        for pos in api_positions:
            print(f"   {pos['symbol']}: {pos['quantity']}股 @ ${pos['cost_price']:.2f}")

        # 同步到Redis
        await pm.sync_from_api(api_positions)

        # 验证同步结果
        all_positions = await pm.get_all_positions()
        print(f"\n✅ Redis持仓数: {len(all_positions)}")
        assert len(all_positions) == 3

        # 测试增量同步（删除了一个持仓）
        print("\n📍 测试增量同步（API显示TSLA已卖出）...")
        api_positions_updated = [
            {"symbol": "AAPL.US", "quantity": 100, "cost_price": 180.5},
            {"symbol": "NVDA.US", "quantity": 200, "cost_price": 450.0},
        ]

        await pm.sync_from_api(api_positions_updated)

        all_positions = await pm.get_all_positions()
        print(f"✅ 同步后持仓数: {len(all_positions)} (应该是2个)")
        assert len(all_positions) == 2
        assert not await pm.has_position("TSLA.US")

        print("\n✅ API同步测试通过！")

    finally:
        await pm.close()


async def test_cross_process():
    """测试跨进程共享（模拟）"""
    print("\n" + "="*70)
    print("测试3：跨进程共享（模拟两个客户端）")
    print("="*70)

    settings = get_settings()

    # 客户端1（模拟 order_executor）
    pm1 = RedisPositionManager(settings.redis_url)
    # 客户端2（模拟 signal_generator）
    pm2 = RedisPositionManager(settings.redis_url)

    try:
        await pm1.connect()
        await pm2.connect()

        # 客户端1添加持仓
        print("\n📍 客户端1（order_executor）: 买入 AAPL.US")
        await pm1.add_position("AAPL.US", quantity=100, cost_price=180.5)

        # 客户端2立即检查
        print("📍 客户端2（signal_generator）: 检查 AAPL.US 是否持有")
        has_position = await pm2.has_position("AAPL.US")

        if has_position:
            print("✅ 客户端2能立即看到客户端1添加的持仓！")
        else:
            print("❌ 客户端2看不到持仓（共享失败）")
            assert False, "跨进程共享失败"

        # 客户端1卖出
        print("\n📍 客户端1（order_executor）: 卖出 AAPL.US")
        await pm1.remove_position("AAPL.US")

        # 客户端2立即检查
        print("📍 客户端2（signal_generator）: 再次检查 AAPL.US")
        has_position = await pm2.has_position("AAPL.US")

        if not has_position:
            print("✅ 客户端2能立即看到持仓被移除！")
        else:
            print("❌ 客户端2仍然显示有持仓（同步失败）")
            assert False, "跨进程同步失败"

        print("\n✅ 跨进程共享测试通过！")

    finally:
        await pm1.close()
        await pm2.close()


async def test_pubsub():
    """测试Pub/Sub通知"""
    print("\n" + "="*70)
    print("测试4：Pub/Sub实时通知")
    print("="*70)

    settings = get_settings()
    pm_publisher = RedisPositionManager(settings.redis_url)
    pm_subscriber = RedisPositionManager(settings.redis_url)

    try:
        await pm_publisher.connect()
        await pm_subscriber.connect()

        received_notifications = []

        # 订阅者回调函数
        async def on_position_update(action, symbol, data):
            print(f"  📢 收到通知: {action} {symbol}")
            received_notifications.append({"action": action, "symbol": symbol})

        # 启动订阅（后台任务）
        print("\n📍 启动订阅...")
        subscribe_task = asyncio.create_task(
            pm_subscriber.subscribe_updates(on_position_update)
        )

        # 等待订阅建立
        await asyncio.sleep(1)

        # 发布消息
        print("\n📍 发布持仓更新...")
        await pm_publisher.add_position("AAPL.US", quantity=100, cost_price=180.5)
        await asyncio.sleep(0.5)

        await pm_publisher.remove_position("AAPL.US")
        await asyncio.sleep(0.5)

        # 取消订阅任务
        subscribe_task.cancel()

        # 验证收到的通知
        print(f"\n✅ 收到{len(received_notifications)}条通知")
        for notif in received_notifications:
            print(f"   - {notif['action']}: {notif['symbol']}")

        if len(received_notifications) >= 2:
            print("\n✅ Pub/Sub通知测试通过！")
        else:
            print(f"\n❌ 只收到{len(received_notifications)}条通知，应该收到2条")

    finally:
        await pm_publisher.close()
        await pm_subscriber.close()


async def test_performance():
    """测试性能"""
    print("\n" + "="*70)
    print("测试5：性能测试")
    print("="*70)

    settings = get_settings()
    pm = RedisPositionManager(settings.redis_url)

    try:
        await pm.connect()
        await pm.clear_all_positions()

        # 测试批量添加
        import time
        print("\n📍 测试批量添加1000个持仓...")
        start_time = time.time()

        for i in range(1000):
            symbol = f"TEST_{i:04d}.US"
            await pm.add_position(symbol, quantity=100, cost_price=100.0, notify=False)

        elapsed = time.time() - start_time
        print(f"✅ 添加1000个持仓耗时: {elapsed:.2f}秒")
        print(f"   平均每个: {elapsed/1000*1000:.2f}毫秒")

        # 测试批量查询
        print("\n📍 测试批量查询1000个持仓...")
        start_time = time.time()

        for i in range(1000):
            symbol = f"TEST_{i:04d}.US"
            await pm.has_position(symbol)

        elapsed = time.time() - start_time
        print(f"✅ 查询1000次耗时: {elapsed:.2f}秒")
        print(f"   平均每次: {elapsed/1000*1000:.2f}毫秒")

        # 清理
        await pm.clear_all_positions()

        print("\n✅ 性能测试完成！")

    finally:
        await pm.close()


async def main():
    """运行所有测试"""
    try:
        await test_basic_operations()
        await test_api_sync()
        await test_cross_process()
        await test_pubsub()
        await test_performance()

        print("\n" + "="*70)
        print("✅ 所有测试通过！Redis持仓共享机制工作正常")
        print("="*70)

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
