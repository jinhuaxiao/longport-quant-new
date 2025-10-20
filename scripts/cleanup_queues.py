#!/usr/bin/env python3
"""
清理Redis队列工具

用途：
1. 清理堆积的处理中信号
2. 清理失败队列
3. 重置整个队列系统

警告：这会删除所有待处理的信号，请谨慎使用！
"""

import asyncio
import sys
from pathlib import Path
from loguru import logger

sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings
from longport_quant.messaging import SignalQueue


async def show_queue_status(queue: SignalQueue):
    """显示队列当前状态"""
    stats = await queue.get_stats()

    print("\n" + "=" * 70)
    print("📊 当前队列状态")
    print("=" * 70)
    print(f"  📥 待处理队列 (main):       {stats['queue_size']} 个信号")
    print(f"  ⚙️  处理中队列 (processing): {stats['processing_size']} 个信号")
    print(f"  ❌ 失败队列 (failed):        {stats['failed_size']} 个信号")
    print("=" * 70)


async def cleanup_processing_queue(queue: SignalQueue):
    """清理处理中队列"""
    print("\n🔄 正在清理处理中队列...")

    try:
        redis = await queue._get_redis()

        # 获取所有处理中的信号
        processing_signals = await redis.zrange(queue.processing_key, 0, -1)
        count = len(processing_signals)

        if count == 0:
            print("✅ 处理中队列为空，无需清理")
            return

        # 删除处理中队列
        await redis.delete(queue.processing_key)

        print(f"✅ 已清理 {count} 个处理中信号")
        logger.info(f"已清理处理中队列: {count} 个信号")

    except Exception as e:
        print(f"❌ 清理失败: {e}")
        logger.error(f"清理处理中队列失败: {e}")


async def cleanup_failed_queue(queue: SignalQueue):
    """清理失败队列"""
    print("\n🔄 正在清理失败队列...")

    try:
        redis = await queue._get_redis()

        # 获取所有失败的信号
        failed_signals = await redis.zrange(queue.failed_key, 0, -1)
        count = len(failed_signals)

        if count == 0:
            print("✅ 失败队列为空，无需清理")
            return

        # 删除失败队列
        await redis.delete(queue.failed_key)

        print(f"✅ 已清理 {count} 个失败信号")
        logger.info(f"已清理失败队列: {count} 个信号")

    except Exception as e:
        print(f"❌ 清理失败: {e}")
        logger.error(f"清理失败队列失败: {e}")


async def cleanup_main_queue(queue: SignalQueue):
    """清理主队列"""
    print("\n🔄 正在清理主队列...")

    try:
        redis = await queue._get_redis()

        # 获取所有待处理的信号
        main_signals = await redis.zrange(queue.queue_key, 0, -1)
        count = len(main_signals)

        if count == 0:
            print("✅ 主队列为空，无需清理")
            return

        # 删除主队列
        await redis.delete(queue.queue_key)

        print(f"✅ 已清理 {count} 个待处理信号")
        logger.info(f"已清理主队列: {count} 个信号")

    except Exception as e:
        print(f"❌ 清理失败: {e}")
        logger.error(f"清理主队列失败: {e}")


async def cleanup_all_queues(queue: SignalQueue):
    """清理所有队列"""
    print("\n🔄 正在清理所有队列...")

    await cleanup_main_queue(queue)
    await cleanup_processing_queue(queue)
    await cleanup_failed_queue(queue)

    print("\n✅ 所有队列已清理完成")


async def move_processing_to_main(queue: SignalQueue):
    """将处理中的信号移回主队列（恢复模式）"""
    print("\n🔄 正在将处理中信号移回主队列...")

    try:
        redis = await queue._get_redis()

        # 获取所有处理中的信号
        processing_signals = await redis.zrange(queue.processing_key, 0, -1)
        count = len(processing_signals)

        if count == 0:
            print("✅ 处理中队列为空，无需移动")
            return

        moved = 0
        for signal_json in processing_signals:
            try:
                signal = queue._deserialize_signal(signal_json)

                # 降低优先级（因为之前处理失败了）
                original_priority = signal.get('score', 0)
                new_priority = original_priority - 20  # 降低20分

                # 重新加入主队列
                await queue.publish_signal(signal, priority=new_priority)
                moved += 1

            except Exception as e:
                logger.warning(f"移动信号失败: {e}")
                continue

        # 清空处理中队列
        await redis.delete(queue.processing_key)

        print(f"✅ 已将 {moved}/{count} 个信号移回主队列（优先级已降低）")
        logger.info(f"已将处理中信号移回主队列: {moved} 个")

    except Exception as e:
        print(f"❌ 移动失败: {e}")
        logger.error(f"移动处理中信号失败: {e}")


async def show_sample_signals(queue: SignalQueue, queue_name: str, limit: int = 10):
    """显示队列中的示例信号"""
    try:
        redis = await queue._get_redis()

        if queue_name == "main":
            key = queue.queue_key
            title = "待处理队列"
        elif queue_name == "processing":
            key = queue.processing_key
            title = "处理中队列"
        elif queue_name == "failed":
            key = queue.failed_key
            title = "失败队列"
        else:
            print(f"❌ 未知队列: {queue_name}")
            return

        signals = await redis.zrange(key, 0, limit - 1, withscores=True)

        if not signals:
            print(f"\n✅ {title}为空")
            return

        print(f"\n{'=' * 70}")
        print(f"📋 {title} - 前{min(limit, len(signals))}个信号")
        print("=" * 70)
        print(f"{'标的':<12} {'类型':<12} {'评分':<8} {'排队时间':<20}")
        print("-" * 70)

        for signal_json, score in signals:
            try:
                signal = queue._deserialize_signal(signal_json)
                symbol = signal.get('symbol', 'N/A')
                signal_type = signal.get('type', 'N/A')
                signal_score = signal.get('score', 0)
                queued_at = signal.get('queued_at', 'N/A')

                if len(queued_at) > 19:
                    queued_at = queued_at[:19]

                print(f"{symbol:<12} {signal_type:<12} {signal_score:<8} {queued_at:<20}")

            except Exception as e:
                print(f"无法解析信号: {e}")
                continue

        total = await redis.zcard(key)
        if total > limit:
            print(f"\n... 还有 {total - limit} 个信号未显示")

        print("=" * 70)

    except Exception as e:
        print(f"❌ 显示失败: {e}")


def print_menu():
    """打印菜单"""
    print("""
╔══════════════════════════════════════════════════════════════╗
║               Redis队列清理工具                               ║
╠══════════════════════════════════════════════════════════════╣
║  1. 查看队列状态                                              ║
║  2. 清理处理中队列（删除）                                    ║
║  3. 清理失败队列（删除）                                      ║
║  4. 清理主队列（删除）                                        ║
║  5. 清理所有队列（危险！）                                    ║
║  6. 将处理中信号移回主队列（恢复模式）                        ║
║  7. 查看待处理信号示例                                        ║
║  8. 查看处理中信号示例                                        ║
║  9. 查看失败信号示例                                          ║
║  0. 退出                                                      ║
╚══════════════════════════════════════════════════════════════╝
""")


async def main():
    """主函数"""
    settings = get_settings()
    queue = SignalQueue(
        redis_url=settings.redis_url,
        queue_key=settings.signal_queue_key,
        processing_key=settings.signal_processing_key,
        failed_key=settings.signal_failed_key,
        max_retries=settings.signal_max_retries
    )

    try:
        # 显示当前状态
        await show_queue_status(queue)

        while True:
            print_menu()
            choice = input("请选择操作 (0-9): ").strip()

            if choice == "0":
                print("\n👋 再见！")
                break

            elif choice == "1":
                await show_queue_status(queue)

            elif choice == "2":
                confirm = input("\n⚠️  确认清理处理中队列？(y/N): ").strip().lower()
                if confirm == 'y':
                    await cleanup_processing_queue(queue)
                    await show_queue_status(queue)
                else:
                    print("❌ 已取消")

            elif choice == "3":
                confirm = input("\n⚠️  确认清理失败队列？(y/N): ").strip().lower()
                if confirm == 'y':
                    await cleanup_failed_queue(queue)
                    await show_queue_status(queue)
                else:
                    print("❌ 已取消")

            elif choice == "4":
                confirm = input("\n⚠️  确认清理主队列？这会删除所有待处理信号！(y/N): ").strip().lower()
                if confirm == 'y':
                    await cleanup_main_queue(queue)
                    await show_queue_status(queue)
                else:
                    print("❌ 已取消")

            elif choice == "5":
                confirm = input("\n⚠️  确认清理所有队列？这是危险操作！(yes/N): ").strip().lower()
                if confirm == 'yes':
                    await cleanup_all_queues(queue)
                    await show_queue_status(queue)
                else:
                    print("❌ 已取消")

            elif choice == "6":
                confirm = input("\n⚠️  确认将处理中信号移回主队列？(y/N): ").strip().lower()
                if confirm == 'y':
                    await move_processing_to_main(queue)
                    await show_queue_status(queue)
                else:
                    print("❌ 已取消")

            elif choice == "7":
                await show_sample_signals(queue, "main", limit=10)

            elif choice == "8":
                await show_sample_signals(queue, "processing", limit=10)

            elif choice == "9":
                await show_sample_signals(queue, "failed", limit=10)

            else:
                print("❌ 无效选择，请重试")

            input("\n按Enter继续...")

    finally:
        await queue.close()


if __name__ == "__main__":
    asyncio.run(main())
