#!/usr/bin/env python3
"""
调试 get_delayed_signals 为什么返回空列表
"""
import asyncio
import sys
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

import redis.asyncio as redis
import json


async def debug():
    print("=" * 70)
    print("调试 get_delayed_signals")
    print("=" * 70)

    # 1. 创建测试信号
    print("\n1️⃣ 创建测试信号...")
    r = await redis.from_url('redis://localhost:6379/0')
    await r.delete('signal_queue:default')

    retry_after = time.time() + 30
    current_time = time.time()

    test_signal = {
        "symbol": "700.HK",
        "side": "BUY",
        "score": 60,
        "retry_after": retry_after,
    }

    signal_json = json.dumps(test_signal, ensure_ascii=False)
    await r.zadd('signal_queue:default', {signal_json: current_time})

    print(f"   ✅ 信号已添加")
    print(f"   retry_after: {retry_after}")
    print(f"   当前时间: {current_time}")
    print(f"   retry_after > 当前时间: {retry_after > current_time}")

    # 2. 直接从 Redis 读取
    print("\n2️⃣ 直接从 Redis 读取...")
    signals = await r.zrange('signal_queue:default', 0, -1)
    print(f"   队列中信号数量: {len(signals)}")

    for i, sig_json in enumerate(signals, 1):
        print(f"\n   信号 [{i}]:")
        print(f"   Raw JSON: {sig_json[:100]}...")

        sig = json.loads(sig_json)
        print(f"   解析后:")
        print(f"      symbol: {sig.get('symbol')}")
        print(f"      retry_after: {sig.get('retry_after')}")
        print(f"      retry_after 类型: {type(sig.get('retry_after'))}")

        if 'retry_after' in sig:
            print(f"      'retry_after' in signal: True")
            retry_ts = sig['retry_after']
            now_ts = time.time()
            print(f"      retry_after ({retry_ts}) > time.time() ({now_ts}): {retry_ts > now_ts}")
        else:
            print(f"      'retry_after' in signal: False")

    # 3. 模拟 get_delayed_signals 的逻辑
    print("\n3️⃣ 模拟 get_delayed_signals 逻辑...")

    delayed_signals = []
    signals = await r.zrange('signal_queue:default', 0, -1)

    print(f"   遍历 {len(signals)} 个信号...")

    for sig_json in signals:
        # 模拟 _deserialize_signal
        sig = json.loads(sig_json)

        print(f"\n   检查信号: {sig.get('symbol')}")

        # 账号过滤（None 时跳过）
        account = None
        if account and sig.get('account') != account:
            print(f"      ❌ 账号不匹配，跳过")
            continue
        else:
            print(f"      ✅ 账号检查通过（account={account}）")

        # 延迟检查
        if 'retry_after' in sig:
            print(f"      ✅ 有 retry_after 字段")
            retry_ts = sig['retry_after']
            now_ts = time.time()
            print(f"         retry_after: {retry_ts}")
            print(f"         time.time(): {now_ts}")
            print(f"         retry_after > time.time(): {retry_ts > now_ts}")

            if retry_ts > now_ts:
                print(f"      ✅ 仍在延迟期，添加到列表")
                delayed_signals.append(sig)
            else:
                print(f"      ❌ 延迟已过期，不添加")
        else:
            print(f"      ❌ 没有 retry_after 字段，不添加")

    print(f"\n   最终结果: {len(delayed_signals)} 个延迟信号")

    # 清理
    await r.delete('signal_queue:default')
    await r.aclose()


if __name__ == "__main__":
    asyncio.run(debug())
