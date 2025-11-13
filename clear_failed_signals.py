#!/usr/bin/env python3
"""清理失败信号队列"""

import asyncio
import sys
from src.longport_quant.messaging.signal_queue import SignalQueue
from datetime import datetime

async def main():
    queue = SignalQueue(redis_url="redis://localhost:6379/0")

    try:
        print("=" * 70)
        print("🔍 失败信号队列检查")
        print("=" * 70)

        # 1. 获取队列统计
        stats = await queue.get_stats()
        print(f"\n📊 队列统计:")
        print(f"  待处理: {stats['queue_size']}")
        print(f"  处理中: {stats['processing_size']}")
        print(f"  失败队列: {stats['failed_size']}")

        if stats['failed_size'] == 0:
            print("\n✅ 失败队列为空，无需清理")
            return

        # 2. 查看失败信号详情
        print(f"\n📋 失败信号详情:")
        redis = await queue._get_redis()
        signals = await redis.zrange(queue.failed_key, 0, -1, withscores=True)

        if not signals:
            print("  无失败信号")
        else:
            print(f"  {'标的':<15} {'类型':<15} {'评分':<8} {'重试次数':<10} {'失败时间':<20}")
            print("  " + "-" * 78)

            for signal_json, failed_timestamp in signals:
                signal = queue._deserialize_signal(signal_json)
                symbol = signal.get('symbol', 'N/A')
                signal_type = signal.get('type', 'N/A')
                score = signal.get('score', 0)
                retry_count = signal.get('retry_count', 0)
                failed_at = datetime.fromtimestamp(failed_timestamp).strftime('%Y-%m-%d %H:%M:%S')

                print(f"  {symbol:<15} {signal_type:<15} {score:<8} {retry_count:<10} {failed_at:<20}")

        # 3. 询问是否清空
        print(f"\n⚠️  确认操作:")
        print(f"   将删除 {stats['failed_size']} 个失败信号")
        print(f"   这些信号不会再被重试")

        # 检查是否有命令行参数
        if len(sys.argv) > 1 and sys.argv[1] == '--yes':
            confirm = 'y'
        else:
            confirm = input("\n   是否继续？(y/N): ").strip().lower()

        if confirm == 'y':
            # 4. 清空失败队列
            count = await queue.clear_queue(queue_type='failed')
            print(f"\n✅ 已清空失败队列，删除了 {count} 个key")

            # 5. 验证清空结果
            stats_after = await queue.get_stats()
            print(f"\n📊 清空后统计:")
            print(f"  待处理: {stats_after['queue_size']}")
            print(f"  处理中: {stats_after['processing_size']}")
            print(f"  失败队列: {stats_after['failed_size']}")
        else:
            print("\n❌ 操作已取消")

        print("\n" + "=" * 70)

    finally:
        await queue.close()

if __name__ == "__main__":
    asyncio.run(main())
