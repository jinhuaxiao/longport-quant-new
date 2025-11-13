#!/usr/bin/env python3
"""测试信号去重逻辑"""

import asyncio
from src.longport_quant.messaging.signal_queue import SignalQueue

async def test_deduplication():
    print("=" * 70)
    print("🧪 测试信号去重逻辑")
    print("=" * 70)

    # 初始化信号队列
    queue = SignalQueue(redis_url="redis://localhost:6379/0")

    try:
        # 1. 清空队列
        print("\n1️⃣ 清空测试队列...")
        redis = await queue._get_redis()
        await redis.delete(queue.queue_key)
        await redis.delete(queue.processing_key)
        print("   ✅ 队列已清空")

        # 2. 发布第一个信号
        signal1 = {
            'symbol': 'CRWV.US',
            'type': 'URGENT_SELL',
            'score': 95,
            'price': 85.55,
            'quantity': 2
        }
        print("\n2️⃣ 发布第一个 CRWV.US URGENT_SELL 信号...")
        await queue.publish_signal(signal1)
        print("   ✅ 信号已发布")

        # 3. 检查队列中是否已存在
        print("\n3️⃣ 检查队列中是否已存在 CRWV.US URGENT_SELL...")
        has_pending = await queue.has_pending_signal('CRWV.US', 'URGENT_SELL')
        print(f"   结果: {has_pending}")
        assert has_pending, "应该返回 True，但返回了 False"
        print("   ✅ 去重检查正常")

        # 4. 尝试发布重复信号（模拟后台任务再次检查）
        print("\n4️⃣ 模拟后台任务尝试发布重复信号...")
        signal2 = {
            'symbol': 'CRWV.US',
            'type': 'URGENT_SELL',
            'score': 95,
            'price': 85.60,
            'quantity': 2
        }

        # 应该先检查是否已存在
        if await queue.has_pending_signal('CRWV.US', 'URGENT_SELL'):
            print("   ⏭️  队列中已有 CRWV.US URGENT_SELL 信号，跳过")
            skipped = True
        else:
            await queue.publish_signal(signal2)
            skipped = False

        assert skipped, "应该跳过重复信号，但没有跳过"
        print("   ✅ 重复信号被正确跳过")

        # 5. 检查队列长度
        print("\n5️⃣ 检查队列长度...")
        queue_size = await queue.get_queue_size()
        print(f"   队列长度: {queue_size}")
        assert queue_size == 1, f"队列长度应该为 1，但实际为 {queue_size}"
        print("   ✅ 队列中只有1个信号（去重成功）")

        # 6. 测试不同信号类型
        print("\n6️⃣ 测试不同信号类型...")
        signal3 = {
            'symbol': 'CRWV.US',
            'type': 'ROTATION_SELL',
            'score': 80,
            'price': 85.55,
            'quantity': 2
        }
        await queue.publish_signal(signal3)
        queue_size = await queue.get_queue_size()
        print(f"   队列长度: {queue_size}")
        assert queue_size == 2, f"队列长度应该为 2，但实际为 {queue_size}"
        print("   ✅ 不同类型的信号可以共存")

        # 7. 测试不同标的
        print("\n7️⃣ 测试不同标的...")
        signal4 = {
            'symbol': 'MARA.US',
            'type': 'URGENT_SELL',
            'score': 95,
            'price': 20.50,
            'quantity': 10
        }
        await queue.publish_signal(signal4)
        queue_size = await queue.get_queue_size()
        print(f"   队列长度: {queue_size}")
        assert queue_size == 3, f"队列长度应该为 3，但实际为 {queue_size}"
        print("   ✅ 不同标的的信号可以共存")

        print("\n" + "=" * 70)
        print("✅ 所有测试通过！")
        print("=" * 70)

    finally:
        # 清理
        await redis.delete(queue.queue_key)
        await redis.delete(queue.processing_key)
        await queue.close()

if __name__ == "__main__":
    asyncio.run(test_deduplication())
