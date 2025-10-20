#!/usr/bin/env python3
"""测试止损管理器的并发访问"""

import asyncio
from loguru import logger
from longport_quant.persistence.stop_manager import StopLossManager


async def concurrent_read_test():
    """测试并发读取"""
    logger.info("=" * 60)
    logger.info("测试止损管理器并发访问")
    logger.info("=" * 60)

    manager = StopLossManager()
    await manager.connect()

    symbols = ["0700.HK", "9988.HK", "3690.HK", "1810.HK", "0981.HK"]

    # 模拟多个任务同时查询
    async def query_symbol(symbol: str, task_id: int):
        for i in range(3):
            try:
                result = await manager.get_stop_for_symbol(symbol)
                logger.info(f"任务{task_id}: 查询 {symbol} 成功 (第{i+1}次) - {result is not None}")
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"任务{task_id}: 查询 {symbol} 失败 (第{i+1}次) - {e}")

    # 创建多个并发任务
    tasks = []
    for idx, symbol in enumerate(symbols):
        task = asyncio.create_task(query_symbol(symbol, idx + 1))
        tasks.append(task)

    # 等待所有任务完成
    await asyncio.gather(*tasks)

    await manager.disconnect()

    logger.info("\n" + "=" * 60)
    logger.success("测试完成 - 如果没有错误则修复成功")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(concurrent_read_test())
