#!/usr/bin/env python3
"""
恢复卡住的信号 - 将processing队列中的僵尸信号移回主队列

场景：
当订单执行器在处理信号时崩溃，信号会被留在processing队列中
这个脚本会将这些"僵尸"信号移回主队列重新处理
"""

import asyncio
import sys
from pathlib import Path
from loguru import logger

# 添加项目根目录到路径
sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings
from longport_quant.messaging import SignalQueue


async def main():
    """主函数"""
    settings = get_settings()

    signal_queue = SignalQueue(
        redis_url=settings.redis_url,
        queue_key=settings.signal_queue_key,
        processing_key=settings.signal_processing_key,
        failed_key=settings.signal_failed_key,
        max_retries=settings.signal_max_retries
    )

    try:
        print("\n" + "="*70)
        print("🔧 恢复卡住的信号")
        print("="*70)

        # 获取统计信息
        stats = await signal_queue.get_stats()
        print(f"\n📊 当前队列状态:")
        print(f"  主队列: {stats['queue_size']} 个信号")
        print(f"  处理中: {stats['processing_size']} 个信号")
        print(f"  失败队列: {stats['failed_size']} 个信号")

        if stats['processing_size'] == 0:
            print("\n✅ 没有需要恢复的信号")
            return

        print(f"\n🔍 发现 {stats['processing_size']} 个卡住的信号")
        print("="*70)

        # 获取processing队列中的所有信号
        redis = await signal_queue._get_redis()
        processing_signals = await redis.zrange(
            signal_queue.processing_key,
            0,
            -1,
            withscores=True
        )

        recovered_count = 0

        for signal_json, score in processing_signals:
            signal = signal_queue._deserialize_signal(signal_json)
            symbol = signal.get('symbol', 'N/A')
            signal_type = signal.get('type', 'N/A')
            signal_score = signal.get('score', 0)

            print(f"\n恢复信号: {symbol}")
            print(f"  类型: {signal_type}")
            print(f"  评分: {signal_score}")
            print(f"  排队时间: {signal.get('queued_at', 'N/A')}")

            # 从processing队列移除
            await redis.zrem(signal_queue.processing_key, signal_json)

            # 重新发布到主队列
            await signal_queue.publish_signal(signal, priority=signal_score)

            recovered_count += 1
            print(f"  ✅ 已移回主队列")

        print("\n" + "="*70)
        print(f"✅ 恢复完成！共恢复 {recovered_count} 个信号")
        print("="*70)

        # 显示恢复后的状态
        stats = await signal_queue.get_stats()
        print(f"\n📊 恢复后的队列状态:")
        print(f"  主队列: {stats['queue_size']} 个信号")
        print(f"  处理中: {stats['processing_size']} 个信号")
        print(f"  失败队列: {stats['failed_size']} 个信号")

        print("\n💡 提示: 现在可以重新启动订单执行器来处理这些信号")
        print("   python3 scripts/order_executor.py")

    except Exception as e:
        logger.error(f"❌ 恢复失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        await signal_queue.close()


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════╗
║           恢复卡住的信号 (Recover Stuck Signals)             ║
╠══════════════════════════════════════════════════════════════╣
║  功能:                                                         ║
║  • 检查processing队列中的僵尸信号                             ║
║  • 将信号移回主队列重新处理                                   ║
║  • 适用于订单执行器崩溃后的恢复                               ║
╚══════════════════════════════════════════════════════════════╝
    """)
    asyncio.run(main())
