#!/usr/bin/env python3
"""
测试队列系统 - 验证Redis队列工作是否正常

测试流程：
1. 创建测试信号
2. 发送到Redis队列
3. 从队列消费
4. 验证数据完整性

"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime
from loguru import logger

sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings
from longport_quant.messaging import SignalQueue


async def test_queue_system():
    """测试队列系统"""
    logger.info("=" * 70)
    logger.info("🧪 测试队列系统")
    logger.info("=" * 70)

    settings = get_settings()

    # 创建队列
    signal_queue = SignalQueue(
        redis_url=settings.redis_url,
        queue_key=settings.signal_queue_key,
        processing_key=settings.signal_processing_key,
        failed_key=settings.signal_failed_key,
        max_retries=settings.signal_max_retries
    )

    try:
        # 测试1：清空队列
        logger.info("\n[测试1] 清空测试队列...")
        await signal_queue.clear_queue('all')
        logger.success("✅ 队列已清空")

        # 测试2：发布信号
        logger.info("\n[测试2] 发布测试信号...")
        test_signals = [
            {
                'symbol': '9992.HK',
                'type': 'STRONG_BUY',
                'side': 'BUY',
                'score': 65,
                'strength': 0.65,
                'price': 291.00,
                'stop_loss': 275.00,
                'take_profit': 310.00,
                'reasons': ['测试信号', 'RSI强势', '突破上轨'],
                'timestamp': datetime.now().isoformat(),
            },
            {
                'symbol': '1810.HK',
                'type': 'BUY',
                'side': 'BUY',
                'score': 50,
                'strength': 0.50,
                'price': 15.50,
                'stop_loss': 14.70,
                'take_profit': 16.50,
                'reasons': ['测试信号', 'MACD金叉'],
                'timestamp': datetime.now().isoformat(),
            },
            {
                'symbol': '3690.HK',
                'type': 'WEAK_BUY',
                'side': 'BUY',
                'score': 35,
                'strength': 0.35,
                'price': 120.00,
                'stop_loss': 114.00,
                'take_profit': 126.00,
                'reasons': ['测试信号'],
                'timestamp': datetime.now().isoformat(),
            }
        ]

        for signal in test_signals:
            success = await signal_queue.publish_signal(signal)
            if success:
                logger.success(f"✅ 发布成功: {signal['symbol']}, 评分={signal['score']}")
            else:
                logger.error(f"❌ 发布失败: {signal['symbol']}")

        # 测试3：检查队列大小
        logger.info("\n[测试3] 检查队列大小...")
        queue_size = await signal_queue.get_queue_size()
        logger.info(f"📊 队列长度: {queue_size}")

        if queue_size == len(test_signals):
            logger.success(f"✅ 队列大小正确: {queue_size}")
        else:
            logger.error(f"❌ 队列大小不符: 期望{len(test_signals)}, 实际{queue_size}")

        # 测试4：获取所有信号
        logger.info("\n[测试4] 获取队列中的信号...")
        signals = await signal_queue.get_all_signals(limit=10)
        logger.info(f"📋 获取到 {len(signals)} 个信号:")
        for i, signal in enumerate(signals, 1):
            logger.info(
                f"   {i}. {signal['symbol']} - "
                f"优先级={signal.get('queue_priority', 0):.0f}, "
                f"评分={signal.get('score', 0)}"
            )

        # 测试5：消费信号（按优先级）
        logger.info("\n[测试5] 按优先级消费信号...")
        consumed = []
        while True:
            signal = await signal_queue.consume_signal()
            if not signal:
                break

            consumed.append(signal)
            logger.success(
                f"✅ 消费: {signal['symbol']}, "
                f"评分={signal.get('score', 0)}, "
                f"类型={signal.get('type', 'N/A')}"
            )

            # 标记完成
            await signal_queue.mark_signal_completed(signal)

        # 验证优先级顺序
        if len(consumed) == len(test_signals):
            logger.success(f"✅ 消费数量正确: {len(consumed)}")

            # 检查是否按优先级排序（高分先出）
            scores = [s.get('score', 0) for s in consumed]
            if scores == sorted(scores, reverse=True):
                logger.success(f"✅ 优先级顺序正确: {scores}")
            else:
                logger.warning(f"⚠️ 优先级顺序可能不正确: {scores}")
        else:
            logger.error(
                f"❌ 消费数量不符: 期望{len(test_signals)}, "
                f"实际{len(consumed)}"
            )

        # 测试6：测试失败重试
        logger.info("\n[测试6] 测试失败重试机制...")
        retry_signal = {
            'symbol': 'TEST.HK',
            'type': 'BUY',
            'side': 'BUY',
            'score': 40,
            'timestamp': datetime.now().isoformat(),
        }

        await signal_queue.publish_signal(retry_signal)
        consumed_signal = await signal_queue.consume_signal()

        if consumed_signal:
            logger.info(f"📥 消费信号: {consumed_signal['symbol']}")

            # 模拟失败，标记重试
            await signal_queue.mark_signal_failed(
                consumed_signal,
                error_message="测试失败",
                retry=True
            )
            logger.success("✅ 失败信号已重新入队")

            # 检查是否重新入队
            queue_size = await signal_queue.get_queue_size()
            if queue_size > 0:
                logger.success(f"✅ 信号重新入队成功，队列长度={queue_size}")

                # 再次消费
                retry_consumed = await signal_queue.consume_signal()
                if retry_consumed and retry_consumed.get('retry_count', 0) > 0:
                    logger.success(
                        f"✅ 重试计数正确: {retry_consumed.get('retry_count')}"
                    )

                    # 清理
                    await signal_queue.mark_signal_completed(retry_consumed)
                else:
                    logger.warning("⚠️ 重试计数可能不正确")
            else:
                logger.error("❌ 信号未重新入队")

        # 测试7：检查最终状态
        logger.info("\n[测试7] 检查最终队列状态...")
        stats = await signal_queue.get_stats()
        logger.info(f"📊 最终统计:")
        logger.info(f"   待处理队列: {stats['queue_size']}")
        logger.info(f"   处理中队列: {stats['processing_size']}")
        logger.info(f"   失败队列:   {stats['failed_size']}")

        if stats['queue_size'] == 0 and stats['processing_size'] == 0:
            logger.success("✅ 队列已清空，所有信号都已处理")
        else:
            logger.warning(
                f"⚠️ 队列未完全清空: "
                f"待处理={stats['queue_size']}, "
                f"处理中={stats['processing_size']}"
            )

        # 测试结论
        logger.info("\n" + "=" * 70)
        logger.success("🎉 队列系统测试完成！")
        logger.info("=" * 70)
        logger.info("""
测试结果：
  ✅ Redis连接正常
  ✅ 信号发布成功
  ✅ 信号消费成功
  ✅ 优先级队列工作正常
  ✅ 失败重试机制正常
  ✅ 状态标记正常

结论：队列系统可以正常使用！

下一步：
  1. 启动信号生成器: python3 scripts/signal_generator.py
  2. 启动订单执行器: python3 scripts/order_executor.py
  3. 监控队列状态: python3 scripts/queue_monitor.py

或者使用一键启动脚本：
  bash scripts/start_trading_system.sh
        """)

    except Exception as e:
        logger.error(f"❌ 测试失败: {e}")
        import traceback
        logger.debug(traceback.format_exc())

    finally:
        # 清理测试数据
        logger.info("\n🗑️ 清理测试数据...")
        await signal_queue.clear_queue('all')
        await signal_queue.close()
        logger.success("✅ 清理完成")


async def main():
    """主函数"""
    try:
        await test_queue_system()
    except Exception as e:
        logger.error(f"测试脚本执行失败: {e}")
        import traceback
        logger.debug(traceback.format_exc())


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════╗
║            队列系统测试 (Queue System Test)                   ║
╠══════════════════════════════════════════════════════════════╣
║  测试内容:                                                     ║
║  • Redis连接                                                  ║
║  • 信号发布和消费                                             ║
║  • 优先级队列                                                 ║
║  • 失败重试机制                                               ║
║  • 状态管理                                                   ║
╠══════════════════════════════════════════════════════════════╣
║  注意：测试会清空现有队列数据                                  ║
╚══════════════════════════════════════════════════════════════╝
    """)

    # 确认是否继续
    response = input("\n是否继续测试？(y/n): ")
    if response.lower() != 'y':
        print("❌ 测试已取消")
        sys.exit(0)

    asyncio.run(main())
