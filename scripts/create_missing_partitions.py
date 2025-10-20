#!/usr/bin/env python3
"""创建缺失的分区表"""
import asyncio
from datetime import datetime, timedelta
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.config import get_settings
from sqlalchemy import text
from loguru import logger


async def create_partitions():
    settings = get_settings()
    async with DatabaseSessionManager(settings.database_dsn) as db:
        async with db.session() as session:
            # 创建未来几个月的分区
            current_date = datetime.now()

            # 创建当前月份和未来3个月的分区
            for months_ahead in range(0, 4):
                partition_date = current_date + timedelta(days=30 * months_ahead)
                year = partition_date.year
                month = partition_date.month
                partition_name = f"kline_minute_{year}_{month:02d}"

                # 计算分区的起止日期
                start_date = f"{year}-{month:02d}-01 00:00:00"
                if month == 12:
                    end_date = f"{year + 1}-01-01 00:00:00"
                else:
                    end_date = f"{year}-{month + 1:02d}-01 00:00:00"

                create_sql = f"""
                    CREATE TABLE IF NOT EXISTS {partition_name}
                    PARTITION OF kline_minute
                    FOR VALUES FROM ('{start_date}')
                    TO ('{end_date}')
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

            # 创建前几个月的分区（用于历史数据）
            for months_back in range(1, 3):
                partition_date = current_date - timedelta(days=30 * months_back)
                year = partition_date.year
                month = partition_date.month
                partition_name = f"kline_minute_{year}_{month:02d}"

                start_date = f"{year}-{month:02d}-01 00:00:00"
                if month == 12:
                    end_date = f"{year + 1}-01-01 00:00:00"
                else:
                    end_date = f"{year}-{month + 1:02d}-01 00:00:00"

                create_sql = f"""
                    CREATE TABLE IF NOT EXISTS {partition_name}
                    PARTITION OF kline_minute
                    FOR VALUES FROM ('{start_date}')
                    TO ('{end_date}')
                """

                try:
                    await session.execute(text(create_sql))
                    await session.commit()
                    logger.info(f"✓ 创建历史分区 {partition_name}")
                except Exception as e:
                    if "already exists" in str(e):
                        logger.info(f"  历史分区 {partition_name} 已存在")
                    else:
                        logger.error(f"✗ 创建历史分区 {partition_name} 失败: {e}")

            # 列出所有分区
            result = await session.execute(
                text("""
                    SELECT tablename
                    FROM pg_tables
                    WHERE schemaname = 'public'
                    AND tablename LIKE 'kline_minute%'
                    ORDER BY tablename
                """)
            )
            partitions = [row[0] for row in result]

            print("\n当前所有kline_minute分区：")
            for p in partitions:
                print(f"  - {p}")


if __name__ == "__main__":
    asyncio.run(create_partitions())