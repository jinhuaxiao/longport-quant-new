#!/usr/bin/env python3
"""
队列监控工具 - 实时监控信号队列状态

显示：
- 队列长度（待处理、处理中、失败）
- 信号列表（优先级排序）
- 处理速率统计
- 失败信号详情

"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from loguru import logger
from typing import List, Dict

# 添加项目根目录到路径
sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings
from longport_quant.messaging import SignalQueue


class QueueMonitor:
    """队列监控器"""

    def __init__(self, refresh_interval: int = 5):
        """
        初始化监控器

        Args:
            refresh_interval: 刷新间隔（秒）
        """
        self.settings = get_settings()
        self.refresh_interval = refresh_interval

        self.signal_queue = SignalQueue(
            redis_url=self.settings.redis_url,
            queue_key=self.settings.signal_queue_key,
            processing_key=self.settings.signal_processing_key,
            failed_key=self.settings.signal_failed_key,
            max_retries=self.settings.signal_max_retries
        )

        # 统计数据
        self.prev_queue_size = 0
        self.prev_timestamp = datetime.now()

    async def run(self):
        """主循环：定期显示队列状态"""
        logger.info("=" * 70)
        logger.info("📊 队列监控器启动")
        logger.info("=" * 70)
        logger.info(f"📥 监控队列: {self.settings.signal_queue_key}")
        logger.info(f"🔄 刷新间隔: {self.refresh_interval}秒")
        logger.info("")

        try:
            iteration = 0
            while True:
                iteration += 1

                # 获取队列统计
                stats = await self.signal_queue.get_stats()
                queue_size = stats['queue_size']
                processing_size = stats['processing_size']
                failed_size = stats['failed_size']

                # 计算处理速率
                now = datetime.now()
                time_elapsed = (now - self.prev_timestamp).total_seconds()
                if time_elapsed > 0 and self.prev_queue_size > 0:
                    signals_processed = self.prev_queue_size - queue_size
                    process_rate = signals_processed / time_elapsed if signals_processed > 0 else 0
                else:
                    process_rate = 0

                # 更新统计
                self.prev_queue_size = queue_size
                self.prev_timestamp = now

                # 显示状态
                print("\n" + "=" * 70)
                print(f"📊 队列状态 (刷新 #{iteration} - {now.strftime('%H:%M:%S')})")
                print("=" * 70)
                print(f"  📥 待处理队列: {queue_size} 个信号")
                print(f"  ⚙️  处理中队列: {processing_size} 个信号")
                print(f"  ❌ 失败队列:   {failed_size} 个信号")
                print(f"  📈 处理速率:   {process_rate:.2f} 信号/秒")
                print("=" * 70)

                # 显示待处理信号列表（前10个）
                if queue_size > 0:
                    signals = await self.signal_queue.get_all_signals(limit=10)

                    print(f"\n📋 待处理信号 (前{min(len(signals), 10)}个):")
                    print("-" * 70)
                    print(f"{'优先级':<8} {'标的':<12} {'类型':<12} {'评分':<6} {'排队时间'}")
                    print("-" * 70)

                    for i, signal in enumerate(signals[:10], 1):
                        priority = signal.get('queue_priority', 0)
                        symbol = signal.get('symbol', 'N/A')
                        signal_type = signal.get('type', 'N/A')
                        score = signal.get('score', 0)
                        queued_at = signal.get('queued_at', 'N/A')

                        # 格式化排队时间
                        if queued_at != 'N/A':
                            try:
                                queued_time = datetime.fromisoformat(queued_at)
                                time_diff = (now - queued_time).total_seconds()
                                if time_diff < 60:
                                    time_str = f"{time_diff:.0f}秒前"
                                elif time_diff < 3600:
                                    time_str = f"{time_diff/60:.0f}分钟前"
                                else:
                                    time_str = f"{time_diff/3600:.1f}小时前"
                            except:
                                time_str = queued_at[-8:]  # 只显示时间部分
                        else:
                            time_str = 'N/A'

                        print(f"{priority:<8.0f} {symbol:<12} {signal_type:<12} {score:<6} {time_str}")

                    if len(signals) < queue_size:
                        print(f"... 还有 {queue_size - len(signals)} 个信号")

                else:
                    print("\n✅ 队列为空，没有待处理信号")

                # 显示失败信号（如果有）
                if failed_size > 0:
                    print(f"\n⚠️ 警告: 发现 {failed_size} 个失败信号")
                    print("   请检查日志或使用 redis-cli 查看详情:")
                    print(f"   redis-cli ZRANGE {self.settings.signal_failed_key} 0 -1 WITHSCORES")

                # 健康检查
                if queue_size > 100:
                    print(f"\n⚠️ 警告: 队列积压严重 ({queue_size} 个信号)")
                    print("   建议:")
                    print("   1. 启动更多 order_executor 实例")
                    print("   2. 检查 order_executor 是否正常运行")
                    print("   3. 检查是否有错误日志")

                if processing_size > 10:
                    print(f"\n⚠️ 警告: 处理中信号过多 ({processing_size} 个)")
                    print("   可能原因:")
                    print("   1. order_executor 执行缓慢")
                    print("   2. 订单执行失败未正确处理")

                # 等待下一次刷新
                await asyncio.sleep(self.refresh_interval)

        except KeyboardInterrupt:
            print("\n\n⚠️ 收到中断信号，正在退出...")
        finally:
            await self.signal_queue.close()


async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='队列监控工具')
    parser.add_argument(
        '--interval',
        type=int,
        default=5,
        help='刷新间隔（秒），默认5秒'
    )
    args = parser.parse_args()

    monitor = QueueMonitor(refresh_interval=args.interval)

    try:
        await monitor.run()
    except Exception as e:
        logger.error(f"❌ 监控器运行失败: {e}")
        import traceback
        logger.error(traceback.format_exc())


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════╗
║               队列监控器 (Queue Monitor)                      ║
╠══════════════════════════════════════════════════════════════╣
║  功能:                                                         ║
║  • 实时显示队列长度                                           ║
║  • 显示待处理信号列表                                         ║
║  • 计算处理速率                                               ║
║  • 检测异常情况并告警                                         ║
╠══════════════════════════════════════════════════════════════╣
║  使用:                                                         ║
║  python3 scripts/queue_monitor.py [--interval 5]              ║
║                                                                ║
║  按 Ctrl+C 退出                                               ║
╚══════════════════════════════════════════════════════════════╝
    """)
    asyncio.run(main())
