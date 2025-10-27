#!/usr/bin/env python3
"""添加备份条件单ID字段到position_stops表"""

import asyncio
import asyncpg
from loguru import logger
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from src.longport_quant.config import get_settings


async def add_backup_order_fields():
    """添加备份条件单ID字段"""
    import os

    # 使用环境变量或默认值
    db_url = os.getenv('DATABASE_DSN', 'postgresql://postgres:jinhua@127.0.0.1:5432/longport_next_new')

    # 转换为asyncpg格式
    if db_url.startswith('postgresql+asyncpg://'):
        db_url = db_url.replace('postgresql+asyncpg://', 'postgresql://')

    conn = await asyncpg.connect(db_url)

    try:
        logger.info("检查并添加备份条件单ID字段...")

        # 检查字段是否已存在
        existing_columns = await conn.fetch("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'position_stops'
            AND column_name IN ('backup_stop_loss_order_id', 'backup_take_profit_order_id')
        """)

        existing_column_names = {row['column_name'] for row in existing_columns}

        # 添加backup_stop_loss_order_id字段
        if 'backup_stop_loss_order_id' not in existing_column_names:
            await conn.execute("""
                ALTER TABLE position_stops
                ADD COLUMN backup_stop_loss_order_id VARCHAR(50)
            """)
            logger.success("✅ 添加字段: backup_stop_loss_order_id")
        else:
            logger.info("  字段已存在: backup_stop_loss_order_id")

        # 添加backup_take_profit_order_id字段
        if 'backup_take_profit_order_id' not in existing_column_names:
            await conn.execute("""
                ALTER TABLE position_stops
                ADD COLUMN backup_take_profit_order_id VARCHAR(50)
            """)
            logger.success("✅ 添加字段: backup_take_profit_order_id")
        else:
            logger.info("  字段已存在: backup_take_profit_order_id")

        # 创建索引以提高查询性能
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_position_stops_backup_stop_order
            ON position_stops(backup_stop_loss_order_id)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_position_stops_backup_profit_order
            ON position_stops(backup_take_profit_order_id)
        """)

        logger.success("✅ 迁移完成")

        # 检查最终表结构
        result = await conn.fetch("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'position_stops'
            ORDER BY ordinal_position
        """)

        logger.info("\n当前表结构:")
        for row in result:
            logger.info(f"  {row['column_name']}: {row['data_type']} (nullable: {row['is_nullable']})")

    except Exception as e:
        logger.error(f"❌ 迁移失败: {e}")
        raise
    finally:
        await conn.close()


async def main():
    logger.info("=" * 70)
    logger.info("开始数据库迁移: 添加备份条件单ID字段")
    logger.info("=" * 70)
    await add_backup_order_fields()
    logger.info("\n✅ 迁移完成！")


if __name__ == "__main__":
    asyncio.run(main())
