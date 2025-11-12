#!/usr/bin/env python3
"""
测试实时挪仓优化后的效果
验证后台任务是否能及时检测到延迟信号并触发挪仓
"""
import asyncio
import sys
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

import redis.asyncio as redis
import json


async def monitor_rotation_trigger():
    """
    监控实时挪仓是否被触发
    """
    print("=" * 70)
    print("监控实时挪仓触发（优化后）")
    print("=" * 70)

    r = await redis.from_url('redis://localhost:6379/0')

    # 监控的队列
    queues = [
        'trading:signals:paper_001',
        'trading:signals:live_001'
    ]

    print("\n📋 当前队列状态:")
    print("-" * 70)

    for queue_key in queues:
        size = await r.zcard(queue_key)
        print(f"\n{queue_key}:")
        print(f"  队列大小: {size}")

        if size > 0:
            signals = await r.zrange(queue_key, 0, -1)

            delayed_count = 0
            rotation_count = 0

            for sig_json in signals:
                sig = json.loads(sig_json)

                signal_type = sig.get('type', '')

                # 统计延迟信号
                if 'retry_after' in sig:
                    retry_ts = sig['retry_after']
                    now_ts = time.time()
                    if retry_ts > now_ts:
                        delayed_count += 1

                # 统计挪仓信号
                if 'ROTATION' in signal_type or signal_type == 'URGENT_SELL':
                    rotation_count += 1

            print(f"  延迟信号数: {delayed_count}")
            print(f"  挪仓/紧急卖出信号数: {rotation_count}")

            # 显示详细信息
            if delayed_count > 0 or rotation_count > 0:
                print(f"\n  详细信息:")
                for sig_json in signals:
                    sig = json.loads(sig_json)
                    symbol = sig.get('symbol')
                    score = sig.get('score')
                    signal_type = sig.get('type')

                    if 'retry_after' in sig and sig['retry_after'] > time.time():
                        wait_seconds = sig['retry_after'] - time.time()
                        print(f"    - {symbol} (延迟): 评分={score}, 还需等待={wait_seconds:.0f}秒")

                    if 'ROTATION' in signal_type or signal_type == 'URGENT_SELL':
                        print(f"    - {symbol} ({signal_type}): 评分={score}")

    # 监控日志文件
    print("\n" + "=" * 70)
    print("📊 监控 signal_generator 日志（最近30行）")
    print("=" * 70)

    import subprocess

    for account in ['paper_001', 'live_001']:
        log_file = f'/data/web/longport-quant-new/logs/signal_generator_{account}.log'

        print(f"\n{account}:")
        print("-" * 70)

        try:
            result = subprocess.run(
                ['tail', '-30', log_file],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')

                # 只显示与实时挪仓相关的行
                rotation_lines = [
                    line for line in lines
                    if any(keyword in line for keyword in [
                        '后台检查',
                        '实时挪仓',
                        '紧急卖出',
                        'rotation_checker_loop',
                        '延迟信号',
                        'ROTATION',
                        'URGENT_SELL'
                    ])
                ]

                if rotation_lines:
                    for line in rotation_lines[-10:]:  # 最后10行
                        print(f"  {line}")
                else:
                    print("  （无相关日志）")
            else:
                print(f"  ⚠️ 无法读取日志文件")

        except Exception as e:
            print(f"  ⚠️ 读取日志失败: {e}")

    await r.aclose()

    # 给出建议
    print("\n" + "=" * 70)
    print("💡 监控建议:")
    print("=" * 70)
    print("1. 如果看到「后台检查」日志，说明后台任务正在运行 ✅")
    print("2. 如果有延迟信号但30秒内未触发挪仓，可能需要检查:")
    print("   - 市场是否开盘")
    print("   - 账户信息是否正常获取")
    print("   - 持仓质量是否足够差（需要满足挪仓条件）")
    print("3. 优化后的系统应该在信号延迟后30-60秒内检测并触发挪仓")


if __name__ == "__main__":
    asyncio.run(monitor_rotation_trigger())
