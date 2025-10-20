#!/usr/bin/env python3
"""创建kline_daily的历史分区"""
import asyncio
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.config import get_settings
from sqlalchemy import text
from loguru import logger


async def create_partitions():
    settings = get_settings()
    async with DatabaseSessionManager(settings.database_dsn) as db:
        async with db.session() as session:
            # 创建2020-2022年的分区（历史数据）
            for year in [2020, 2021, 2022]:
                partition_name = f"kline_daily_{year}"
                create_sql = f"""
                    CREATE TABLE IF NOT EXISTS {partition_name}
                    PARTITION OF kline_daily
                    FOR VALUES FROM ('{year}-01-01')
                    TO ('{year + 1}-01-01')
                """

                try:
                    await session.execute(text(create_sql))
                    await session.commit()
                    logger.info(f"✓ 创建分区 {partition_name}")
                except Exception as e:
                    if "already exists" in str(e):
                        logger.info(f"  分区 {partition_name} 已存在")
                    else:
                        logger.error(f"✗ 创建分区 {partition_name} 失败: {e}")

            # 列出所有分区
            result = await session.execute(
                text("""
                    SELECT tablename
                    FROM pg_tables
                    WHERE schemaname = 'public'
                    AND tablename LIKE 'kline_daily%'
                    ORDER BY tablename
                """)
            )
            partitions = [row[0] for row in result]

            print("\n当前所有kline_daily分区：")
            for p in partitions:
                print(f"  - {p}")


if __name__ == "__main__":
    asyncio.run(create_partitions())