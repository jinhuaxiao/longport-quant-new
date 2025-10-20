#!/usr/bin/env python3
"""测试信号队列和处理器"""

import asyncio
from loguru import logger


async def test_signal_queue():
    """测试信号队列是否正常工作"""
    logger.info("=" * 60)
    logger.info("测试信号队列和处理器")
    logger.info("=" * 60)

    # 创建优先级队列
    signal_queue = asyncio.PriorityQueue()

    # 测试数据
    test_signals = [
        {"symbol": "0700.HK", "score": 60, "name": "腾讯"},
        {"symbol": "1810.HK", "score": 47, "name": "小米"},
        {"symbol": "9988.HK", "score": 47, "name": "阿里巴巴"},
        {"symbol": "3690.HK", "score": 55, "name": "美团"},
    ]

    # 信号处理器
    async def signal_processor():
        logger.info("🚀 启动信号处理器...")
        count = 0
        while count < len(test_signals):
            try:
                logger.debug("⏳ 等待信号队列...")
                priority, signal_data = await asyncio.wait_for(
                    signal_queue.get(),
                    timeout=5.0
                )
                logger.success(f"📥 收到信号 #{count+1}: {signal_data['symbol']} ({signal_data['name']}), 评分={signal_data['score']}, 优先级={-priority}")
                count += 1
                await asyncio.sleep(0.5)  # 模拟处理
            except asyncio.TimeoutError:
                logger.error("❌ 信号队列超时，5秒内没有收到信号")
                break
            except Exception as e:
                logger.error(f"❌ 信号处理器错误: {e}")
                break

        logger.info(f"✅ 处理完成，共处理 {count} 个信号")

    # 启动信号处理器
    processor_task = asyncio.create_task(signal_processor())

    # 等待处理器启动
    await asyncio.sleep(0.5)

    # 入队测试信号
    logger.info("\n📤 开始入队测试信号...")
    for signal in test_signals:
        priority = -signal['score']  # 负数表示高优先级
        await signal_queue.put((priority, signal))
        logger.info(f"✅ 入队: {signal['symbol']} ({signal['name']}), 评分={signal['score']}")
        await asyncio.sleep(0.2)

    # 等待处理完成
    await processor_task

    logger.info("\n" + "=" * 60)
    logger.info("测试完成")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_signal_queue())
