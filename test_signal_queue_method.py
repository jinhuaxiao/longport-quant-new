#!/usr/bin/env python3
"""
直接测试 SignalQueue.get_delayed_signals() 方法
"""
import asyncio
import sys
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from longport_quant.messaging.signal_queue import SignalQueue
import redis.asyncio as redis
import json


async def test():
    print("=" * 70)
    print("测试 SignalQueue.get_delayed_signals()")
    print("=" * 70)

    # 1. 准备测试数据
    print("\n1️⃣ 准备测试数据...")
    r = await redis.from_url('redis://localhost:6379/0')
    await r.delete('signal_queue:default')

    retry_after = time.time() + 30

    test_signal = {
        "symbol": "700.HK",
        "side": "BUY",
        "score": 60,
        "retry_after": retry_after,
    }

    signal_json = json.dumps(test_signal, ensure_ascii=False)
    await r.zadd('signal_queue:default', {signal_json: time.time()})
    print(f"   ✅ 测试信号已添加 (retry_after={retry_after})")

    # 2. 验证 Redis 中的数据
    print("\n2️⃣ 验证 Redis 中的数据...")
    count = await r.zcard('signal_queue:default')
    print(f"   队列大小: {count}")

    # 3. 调用 SignalQueue.get_delayed_signals()
    print("\n3️⃣ 调用 SignalQueue.get_delayed_signals()...")
    signal_queue = SignalQueue()

    try:
        delayed = await signal_queue.get_delayed_signals(account=None)
        print(f"   返回结果: {len(delayed)} 个延迟信号")

        if delayed:
            for sig in delayed:
                print(f"   - {sig.get('symbol')}: 评分={sig.get('score')}, retry_after={sig.get('retry_after')}")
        else:
            print("   ❌ 返回空列表")

            # 检查是否有异常被捕获
            print("\n   手动执行 get_delayed_signals 逻辑进行调试...")

            redis_client = await signal_queue._get_redis()
            signals = await redis_client.zrange(signal_queue.queue_key, 0, -1)

            print(f"   从 Redis 读取到 {len(signals)} 个信号")

            for sig_json in signals:
                print(f"\n   处理信号...")
                print(f"   - Raw: {sig_json[:100]}")

                sig = signal_queue._deserialize_signal(sig_json)
                print(f"   - 解析后: {sig}")

                # 检查条件
                has_retry_after = 'retry_after' in sig
                print(f"   - 'retry_after' in signal: {has_retry_after}")

                if has_retry_after:
                    retry_ts = sig['retry_after']
                    now_ts = time.time()
                    is_delayed = retry_ts > now_ts

                    print(f"   - retry_after ({retry_ts}) > time.time() ({now_ts}): {is_delayed}")

    except Exception as e:
        print(f"   ❌ 异常: {e}")
        import traceback
        traceback.print_exc()

    # 清理
    await r.delete('signal_queue:default')
    await r.aclose()


if __name__ == "__main__":
    asyncio.run(test())
