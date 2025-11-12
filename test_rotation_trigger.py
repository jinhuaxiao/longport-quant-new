#!/usr/bin/env python3
"""
测试实时挪仓触发机制
模拟一个高分延迟信号，看看实时挪仓是否会触发
"""
import asyncio
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent))

from longport_quant.config import get_settings
from longport_quant.messaging.signal_queue import SignalQueue
import redis.asyncio as redis
import json


async def test_rotation_trigger():
    settings = get_settings()
    signal_queue = SignalQueue()

    print("=" * 70)
    print("测试实时挪仓触发机制")
    print("=" * 70)

    # 1. 清空队列
    print("\n1️⃣ 清空现有队列...")
    r = await redis.from_url('redis://localhost:6379/0')
    await r.delete('signal_queue:default')
    print("   ✅ 队列已清空")

    # 2. 创建一个模拟的高分延迟信号
    print("\n2️⃣ 创建模拟的高分延迟信号...")

    # 延迟到 30 秒后
    retry_after = time.time() + 30

    test_signal = {
        "symbol": "700.HK",
        "side": "BUY",
        "type": "BUY",
        "score": 60,
        "account": settings.account_id,  # 使用配置的 account_id（可能是 None）
        "retry_after": retry_after,
        "retry_count": 1,
        "market": "HK",
        "reason": "测试实时挪仓",
        "timestamp": datetime.now().isoformat()
    }

    # 添加到队列
    signal_json = json.dumps(test_signal, ensure_ascii=False)
    await r.zadd('signal_queue:default', {signal_json: time.time()})

    print(f"   ✅ 已添加测试信号:")
    print(f"      标的: {test_signal['symbol']}")
    print(f"      评分: {test_signal['score']}")
    print(f"      账号: {test_signal['account']}")
    print(f"      retry_after: {retry_after} ({datetime.fromtimestamp(retry_after).strftime('%H:%M:%S')})")
    print(f"      当前时间: {time.time()} ({datetime.now().strftime('%H:%M:%S')})")
    print(f"      仍在延迟: {retry_after > time.time()} (还需等待 {retry_after - time.time():.0f} 秒)")

    # 3. 测试 get_delayed_signals()
    print("\n3️⃣ 测试 get_delayed_signals()...")

    # 测试不带账号过滤
    delayed_no_filter = await signal_queue.get_delayed_signals(account=None)
    print(f"   无账号过滤: {len(delayed_no_filter)} 个延迟信号")
    if delayed_no_filter:
        for sig in delayed_no_filter:
            print(f"      - {sig.get('symbol')}: 评分={sig.get('score')}")

    # 测试带账号过滤
    if settings.account_id:
        delayed_with_filter = await signal_queue.get_delayed_signals(account=settings.account_id)
        print(f"   账号过滤 ({settings.account_id}): {len(delayed_with_filter)} 个延迟信号")
    else:
        print(f"   账号过滤: 跳过（account_id 未配置）")

    # 4. 测试实时挪仓的筛选逻辑
    print("\n4️⃣ 测试实时挪仓的高分信号筛选...")

    delayed_signals = await signal_queue.get_delayed_signals(account=settings.account_id)

    min_score = getattr(settings, 'realtime_rotation_min_signal_score', 60)
    high_score_delayed = [
        s for s in delayed_signals
        if s.get('score', 0) >= min_score
        and s.get('side') == 'BUY'
    ]

    print(f"   最低评分要求: {min_score}")
    print(f"   符合条件的高分延迟信号: {len(high_score_delayed)} 个")

    if high_score_delayed:
        print("   ✅ 实时挪仓应该被触发！")
        for sig in high_score_delayed:
            print(f"      - {sig.get('symbol')}: 评分={sig.get('score')}, 方向={sig.get('side')}")
    else:
        print("   ❌ 实时挪仓不会被触发")
        print("   可能原因:")
        if not delayed_signals:
            print("      - get_delayed_signals() 返回空列表")
            if settings.account_id is None:
                print("      - account_id 未配置，但信号可能有 account 字段")
            else:
                print(f"      - account 不匹配（需要: {settings.account_id}，信号中: {test_signal['account']}）")
        else:
            for sig in delayed_signals:
                if sig.get('score', 0) < min_score:
                    print(f"      - {sig.get('symbol')}: 评分太低 ({sig.get('score')} < {min_score})")
                if sig.get('side') != 'BUY':
                    print(f"      - {sig.get('symbol')}: 不是买入信号 (side={sig.get('side')})")

    # 5. 清理
    print("\n5️⃣ 清理测试数据...")
    await r.delete('signal_queue:default')
    await r.aclose()
    print("   ✅ 测试完成")

    # 6. 结论
    print("\n" + "=" * 70)
    print("📊 测试结论:")
    print("=" * 70)

    if len(high_score_delayed) > 0:
        print("✅ 实时挪仓机制正常，会被触发")
    else:
        print("❌ 实时挪仓机制存在问题，不会被触发")
        print("\n建议修复:")
        if settings.account_id is None and test_signal.get('account') is not None:
            print("1. 在 .env 中配置 ACCOUNT_ID")
        if settings.account_id and test_signal.get('account') != settings.account_id:
            print("1. 确保信号的 account 字段与 settings.account_id 一致")
        if not delayed_signals:
            print("2. 检查 retry_after 时间戳是否正确设置且未过期")


if __name__ == "__main__":
    asyncio.run(test_rotation_trigger())
