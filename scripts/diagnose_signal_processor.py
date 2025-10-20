#!/usr/bin/env python3
"""诊断信号处理器问题"""

import asyncio
import sys
from loguru import logger


async def test_queue_basic():
    """测试基本的队列操作"""
    logger.info("测试1: 基本队列操作")

    queue = asyncio.Queue()

    # 入队
    await queue.put((50, {"symbol": "TEST", "data": "test"}))
    logger.success("✅ 入队成功")

    # 出队
    try:
        priority, data = await asyncio.wait_for(queue.get(), timeout=1.0)
        logger.success(f"✅ 出队成功: priority={priority}, data={data}")
    except asyncio.TimeoutError:
        logger.error("❌ 出队超时")
    except Exception as e:
        logger.error(f"❌ 出队失败: {e}")


async def test_processor_startup():
    """测试信号处理器启动"""
    logger.info("\n测试2: 信号处理器启动")

    queue = asyncio.Queue()
    processed_count = 0

    async def processor():
        nonlocal processed_count
        logger.info("🚀 处理器启动")
        while processed_count < 3:
            try:
                logger.debug("⏳ 等待信号...")
                item = await asyncio.wait_for(queue.get(), timeout=5.0)

                # 尝试解包
                if isinstance(item, tuple) and len(item) == 2:
                    priority, data = item
                    symbol = data.get('symbol', 'UNKNOWN')
                    logger.success(f"✅ 处理信号: {symbol}, priority={priority}")
                    processed_count += 1
                else:
                    logger.error(f"❌ 格式错误: {type(item)}, {item}")
                    break

            except asyncio.TimeoutError:
                logger.error("❌ 处理器超时（5秒内没有信号）")
                break
            except Exception as e:
                logger.error(f"❌ 处理器异常: {type(e).__name__}: {e}")
                import traceback
                traceback.print_exc()
                break

        logger.info(f"处理器结束，共处理 {processed_count} 个信号")

    # 启动处理器
    task = asyncio.create_task(processor())

    # 等待启动
    await asyncio.sleep(0.5)

    # 入队测试数据
    logger.info("入队测试信号...")
    test_signals = [
        (50, {"symbol": "0700.HK", "score": 50}),
        (47, {"symbol": "1810.HK", "score": 47}),
        (55, {"symbol": "3690.HK", "score": 55}),
    ]

    for priority, data in test_signals:
        await queue.put((priority, data))
        logger.info(f"✅ 入队: {data['symbol']}, priority={priority}")
        await asyncio.sleep(0.2)

    # 等待处理完成
    await task


async def test_exception_in_processor():
    """测试处理器中的异常处理"""
    logger.info("\n测试3: 处理器异常处理")

    queue = asyncio.Queue()

    async def buggy_processor():
        logger.info("🚀 启动有bug的处理器")
        try:
            while True:
                logger.debug("⏳ 等待信号...")
                item = await asyncio.wait_for(queue.get(), timeout=2.0)

                priority, data = item
                symbol = data['symbol']
                logger.info(f"📥 收到信号: {symbol}")

                # 模拟处理
                if symbol == "ERROR":
                    raise ValueError("模拟异常")

                logger.success(f"✅ 处理完成: {symbol}")

        except asyncio.TimeoutError:
            logger.warning("超时退出")
        except Exception as e:
            logger.error(f"❌ 处理器崩溃: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

    task = asyncio.create_task(buggy_processor())
    await asyncio.sleep(0.5)

    # 入队正常信号
    await queue.put((50, {"symbol": "GOOD"}))
    await asyncio.sleep(0.5)

    # 入队会导致异常的信号
    await queue.put((50, {"symbol": "ERROR"}))
    await asyncio.sleep(0.5)

    # 看看处理器是否还活着
    await queue.put((50, {"symbol": "AFTER_ERROR"}))
    await asyncio.sleep(0.5)

    # 取消任务
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def main():
    logger.info("=" * 60)
    logger.info("信号处理器诊断工具")
    logger.info("=" * 60)

    try:
        await test_queue_basic()
        await test_processor_startup()
        await test_exception_in_processor()

        logger.info("\n" + "=" * 60)
        logger.success("所有测试完成")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
