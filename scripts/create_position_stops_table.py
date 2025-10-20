#!/usr/bin/env python3
"""创建持仓止损止盈表"""

import asyncio
import asyncpg
from loguru import logger
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from src.longport_quant.config import get_settings


async def create_position_stops_table():
    """创建持仓止损止盈表"""
    import os

    # 使用环境变量或默认值
    db_url = os.getenv('DATABASE_DSN', 'postgresql://postgres:jinhua@127.0.0.1:5432/longport_next_new')

    # 转换为asyncpg格式
    if db_url.startswith('postgresql+asyncpg://'):
        db_url = db_url.replace('postgresql+asyncpg://', 'postgresql://')

    conn = await asyncpg.connect(db_url)

    try:
        # 创建止损止盈表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS position_stops (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                entry_price DECIMAL(10, 2) NOT NULL,
                stop_loss DECIMAL(10, 2) NOT NULL,
                take_profit DECIMAL(10, 2) NOT NULL,
                atr DECIMAL(10, 2),
                quantity INTEGER,
                strategy VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status VARCHAR(20) DEFAULT 'active',  -- active, stopped_out, took_profit, cancelled
                exit_price DECIMAL(10, 2),
                exit_time TIMESTAMP,
                pnl DECIMAL(10, 2),
                UNIQUE(symbol, status)  -- 每个标的只能有一个活跃的止损止盈设置
            )
        """)

        # 创建索引
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_position_stops_symbol ON position_stops(symbol)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_position_stops_status ON position_stops(status)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_position_stops_created ON position_stops(created_at)")

        logger.success("成功创建position_stops表")

        # 检查表结构
        result = await conn.fetch("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'position_stops'
            ORDER BY ordinal_position
        """)

        logger.info("\n表结构:")
        for row in result:
            logger.info(f"  {row['column_name']}: {row['data_type']} (nullable: {row['is_nullable']})")

    except Exception as e:
        logger.error(f"创建表失败: {e}")
        raise
    finally:
        await conn.close()


async def main():
    logger.info("开始创建持仓止损止盈表...")
    await create_position_stops_table()
    logger.info("完成！")


if __name__ == "__main__":
    asyncio.run(main())