#!/usr/bin/env python3
"""
测试失败信号检测和恢复功能
模拟一个刚失败的高分信号，验证实时挪仓能否检测到并处理
"""
import asyncio
import sys
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

import redis.asyncio as redis
import json


async def test_failed_signal_detection():
    print("=" * 70)
    print("测试失败信号检测和恢复")
    print("=" * 70)

    r = await redis.from_url('redis://localhost:6379/0')

    # 1. 创建一个模拟的失败信号（刚失败，在5分钟窗口内）
    print("\n1️⃣ 创建测试失败信号...")

    failed_key = 'trading:signals:failed:paper_001'
    current_time = time.time()

    test_signal = {
        "symbol": "TEST.HK",
        "side": "BUY",
        "score": 75,  # 高分信号
        "account": "paper_001",  # 明确设置account
        "type": "BUY",
        "error": "Insufficient funds for minimum lot",
        "created_at": current_time - 60  # 1分钟前失败
    }

    signal_json = json.dumps(test_signal, ensure_ascii=False)
    await r.zadd(failed_key, {signal_json: current_time - 60})

    print(f"   ✅ 测试信号已添加到失败队列")
    print(f"   Symbol: {test_signal['symbol']}")
    print(f"   Score: {test_signal['score']}")
    print(f"   Account: {test_signal['account']}")
    print(f"   失败时间: 1分钟前")

    # 2. 等待后台任务检测（最多等待35秒）
    print("\n2️⃣ 等待后台任务检测（最多35秒）...")
    print("   后台任务每30秒运行一次，请耐心等待...")

    for i in range(35):
        await asyncio.sleep(1)

        # 检查信号是否还在失败队列中
        signals = await r.zrange(failed_key, 0, -1)
        test_signal_found = any(b'TEST.HK' in sig for sig in signals)

        if not test_signal_found:
            print(f"\n   ✅ 测试信号已从失败队列移除（{i+1}秒后）")
            break

        if (i + 1) % 5 == 0:
            print(f"   ⏳ 已等待 {i+1} 秒...")
    else:
        print("\n   ⚠️  等待超时，信号仍在失败队列中")

    # 3. 检查最终状态
    print("\n3️⃣ 检查最终状态...")

    # 检查失败队列
    signals = await r.zrange(failed_key, 0, -1)
    test_signal_in_failed = any(b'TEST.HK' in sig for sig in signals)

    # 检查主队列
    main_key = 'trading:signals:paper_001'
    signals = await r.zrange(main_key, 0, -1)
    test_signal_in_main = any(b'TEST.HK' in sig for sig in signals)

    # 检查处理队列
    processing_key = 'trading:signals:processing:paper_001'
    signals = await r.zrange(processing_key, 0, -1)
    test_signal_in_processing = any(b'TEST.HK' in sig for sig in signals)

    print(f"   失败队列: {'❌' if not test_signal_in_failed else '✅'} {'已移除' if not test_signal_in_failed else '仍存在'}")
    print(f"   主队列: {'✅' if test_signal_in_main else '❌'} {'已恢复' if test_signal_in_main else '不存在'}")
    print(f"   处理队列: {'✅' if test_signal_in_processing else '❌'} {'正在处理' if test_signal_in_processing else '不存在'}")

    # 4. 查看日志
    print("\n4️⃣ 查看相关日志...")

    import subprocess
    result = subprocess.run(
        ['tail', '-50', '/data/web/longport-quant-new/logs/signal_generator_paper_001.log'],
        capture_output=True,
        text=True,
        timeout=5
    )

    if result.returncode == 0:
        lines = result.stdout.strip().split('\n')
        relevant_lines = [
            line for line in lines
            if any(keyword in line for keyword in [
                'TEST.HK',
                '失败信号',
                '恢复',
                'recover',
                '检测到.*高分信号',
                '实时挪仓'
            ])
        ]

        if relevant_lines:
            print("   相关日志:")
            for line in relevant_lines[-10:]:
                print(f"   {line}")
        else:
            print("   ⚠️  未找到相关日志")

    # 5. 清理测试数据
    print("\n5️⃣ 清理测试数据...")

    # 从所有队列中移除测试信号
    for key in [failed_key, main_key, processing_key]:
        signals = await r.zrange(key, 0, -1)
        for sig in signals:
            if b'TEST.HK' in sig:
                await r.zrem(key, sig)
                print(f"   ✅ 已从 {key.split(':')[-1]} 队列移除测试信号")

    await r.aclose()

    print("\n" + "=" * 70)
    print("测试完成")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_failed_signal_detection())
