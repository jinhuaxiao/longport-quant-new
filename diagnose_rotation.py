#!/usr/bin/env python3
"""
诊断实时挪仓问题
"""
import asyncio
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from longport_quant.config import get_settings
from longport_quant.messaging.signal_queue import SignalQueue


async def diagnose():
    settings = get_settings()
    signal_queue = SignalQueue()

    print("=" * 70)
    print("实时挪仓诊断")
    print("=" * 70)

    # 1. 检查配置
    print("\n1️⃣ 配置检查:")
    print(f"   account_id: {settings.account_id}")
    print(f"   realtime_rotation_enabled: {settings.realtime_rotation_enabled}")
    print(f"   realtime_rotation_min_signal_score: {settings.realtime_rotation_min_signal_score}")
    print(f"   realtime_rotation_min_score_diff: {settings.realtime_rotation_min_score_diff}")

    # 2. 检查队列
    print("\n2️⃣ 队列状态:")
    import redis.asyncio as redis
    r = await redis.from_url('redis://localhost:6379/0')
    queue_size = await r.zcard('signal_queue:default')
    print(f"   队列大小: {queue_size}")

    # 3. 检查延迟信号（不过滤账号）
    print("\n3️⃣ 延迟信号检查（无账号过滤）:")
    delayed_signals_no_filter = await signal_queue.get_delayed_signals(account=None)
    print(f"   延迟信号数量: {len(delayed_signals_no_filter)}")

    if delayed_signals_no_filter:
        for sig in delayed_signals_no_filter:
            print(f"\n   信号详情:")
            print(f"   - 标的: {sig.get('symbol')}")
            print(f"   - 评分: {sig.get('score')}")
            print(f"   - 账号: {sig.get('account')}")
            print(f"   - retry_after: {sig.get('retry_after')}")
            if 'retry_after' in sig:
                now = datetime.now().timestamp()
                retry_time = sig['retry_after']
                print(f"   - 当前时间: {now:.2f}")
                print(f"   - 重试时间: {retry_time:.2f}")
                print(f"   - 仍在延迟: {retry_time > now} (差值: {retry_time - now:.2f}秒)")

    # 4. 检查延迟信号（使用配置的账号）
    if settings.account_id:
        print(f"\n4️⃣ 延迟信号检查（账号={settings.account_id}）:")
        delayed_signals_with_filter = await signal_queue.get_delayed_signals(
            account=settings.account_id
        )
        print(f"   延迟信号数量: {len(delayed_signals_with_filter)}")
    else:
        print("\n4️⃣ 延迟信号检查（跳过，因为account_id未配置）")

    # 5. 直接查看队列中的所有信号
    print("\n5️⃣ 队列中所有信号（前5个）:")
    signals = await r.zrange('signal_queue:default', 0, 4)

    if not signals:
        print("   队列为空")
    else:
        import json
        for i, signal_json in enumerate(signals, 1):
            signal = json.loads(signal_json)
            print(f"\n   [{i}] {signal.get('symbol')}:")
            print(f"       评分: {signal.get('score')}")
            print(f"       类型: {signal.get('type')}")
            print(f"       账号: {signal.get('account')}")
            print(f"       retry_after: {signal.get('retry_after')}")
            if 'retry_after' in signal:
                now = datetime.now().timestamp()
                is_delayed = signal['retry_after'] > now
                print(f"       延迟状态: {'是' if is_delayed else '否'}")

    await r.aclose()

    # 6. 建议
    print("\n" + "=" * 70)
    print("📋 诊断建议:")
    print("=" * 70)

    if settings.account_id is None:
        print("⚠️  account_id 未配置，这可能导致账号过滤失效")
        print("   建议: 在 .env 中添加 ACCOUNT_ID=<你的账号ID>")

    if queue_size == 0:
        print("⚠️  队列为空，没有待处理的信号")
        print("   可能原因: 信号已被处理，或尚未生成新信号")

    if len(delayed_signals_no_filter) == 0 and queue_size > 0:
        print("⚠️  队列中有信号，但没有处于延迟状态的信号")
        print("   可能原因:")
        print("   1. 信号的 retry_after 时间已过期")
        print("   2. 信号没有 retry_after 字段")
        print("   3. 信号从未被标记为延迟")


if __name__ == "__main__":
    asyncio.run(diagnose())
