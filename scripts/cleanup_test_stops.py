#!/usr/bin/env python3
"""清理测试止损数据"""

import asyncio
from loguru import logger
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from src.longport_quant.persistence.stop_manager import StopLossManager


async def main():
    logger.info("="*70)
    logger.info("清理测试止损止盈数据")
    logger.info("="*70)

    stop_manager = StopLossManager()

    try:
        # 连接数据库
        await stop_manager.connect()

        # 1. 查询所有TEST开头的记录
        logger.info("\n查询TEST开头的记录...")
        rows = await stop_manager.conn.fetch("""
            SELECT symbol, status, entry_price, stop_loss, take_profit, created_at
            FROM position_stops
            WHERE symbol LIKE 'TEST%'
            ORDER BY created_at DESC
        """)

        if not rows:
            logger.info("✅ 没有找到TEST开头的记录")
            return

        logger.info(f"\n找到 {len(rows)} 条TEST记录:")
        for row in rows:
            logger.info(
                f"  {row['symbol']}: status={row['status']}, "
                f"entry=${row['entry_price']:.2f}, "
                f"stop=${row['stop_loss']:.2f}, "
                f"take_profit=${row['take_profit']:.2f}, "
                f"created={row['created_at']}"
            )

        # 2. 删除这些记录
        logger.info("\n删除TEST记录...")
        result = await stop_manager.conn.execute("""
            DELETE FROM position_stops
            WHERE symbol LIKE 'TEST%'
        """)

        logger.success(f"✅ 成功删除 {result.split()[-1]} 条TEST记录")

        # 3. 显示剩余的活跃止损记录
        logger.info("\n查询剩余的活跃止损记录...")
        active_rows = await stop_manager.conn.fetch("""
            SELECT symbol, entry_price, stop_loss, take_profit
            FROM position_stops
            WHERE status = 'active'
            ORDER BY symbol
        """)

        if active_rows:
            logger.info(f"\n剩余 {len(active_rows)} 个活跃止损记录:")
            for row in active_rows:
                logger.info(
                    f"  {row['symbol']}: "
                    f"entry=${row['entry_price']:.2f}, "
                    f"stop=${row['stop_loss']:.2f}, "
                    f"take_profit=${row['take_profit']:.2f}"
                )
        else:
            logger.info("✅ 没有剩余的活跃止损记录")

    except Exception as e:
        logger.error(f"清理失败: {e}")
        import traceback
        traceback.print_exc()

    finally:
        await stop_manager.disconnect()

    logger.info("\n清理完成！")


if __name__ == "__main__":
    asyncio.run(main())
