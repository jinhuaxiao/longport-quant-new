#!/usr/bin/env python3
"""检查分区表情况"""
import asyncio
from datetime import datetime
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.config import get_settings
from sqlalchemy import text


async def check_partitions():
    settings = get_settings()
    async with DatabaseSessionManager(settings.database_dsn) as db:
        async with db.session() as session:
            # 查看kline_minute的分区
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
            print(f"kline_minute分区表：")
            for p in partitions:
                print(f"  - {p}")

            # 检查当前时间需要哪个分区
            current_time = datetime.now()
            year_month = current_time.strftime("%Y_%m")
            expected_partition = f"kline_minute_{year_month}"

            print(f"\n当前时间：{current_time}")
            print(f"需要的分区：{expected_partition}")

            if expected_partition not in partitions:
                print(f"⚠️  缺少当前月份的分区 {expected_partition}")

                # 创建缺失的分区
                create_sql = f"""
                    CREATE TABLE IF NOT EXISTS {expected_partition}
                    PARTITION OF kline_minute
                    FOR VALUES FROM ('{current_time.year}-{current_time.month:02d}-01 00:00:00')
                    TO ('{current_time.year}-{(current_time.month % 12) + 1:02d}-01 00:00:00')
                """
                print(f"\n建议创建分区的SQL：")
                print(create_sql)
            else:
                print(f"✓ 分区存在")


if __name__ == "__main__":
    asyncio.run(check_partitions())