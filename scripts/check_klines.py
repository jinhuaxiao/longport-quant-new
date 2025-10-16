#!/usr/bin/env python3
"""检查K线数据"""
import asyncio
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.config import get_settings
from sqlalchemy import text


async def check():
    settings = get_settings()
    async with DatabaseSessionManager(settings.database_dsn) as db:
        async with db.session() as session:
            # 检查分钟线数据
            result = await session.execute(text('SELECT COUNT(*) FROM kline_minute'))
            count = result.scalar()
            print(f'kline_minute表中有 {count} 条记录')

            if count > 0:
                result = await session.execute(
                    text('SELECT symbol, timestamp, close FROM kline_minute ORDER BY timestamp DESC LIMIT 5')
                )
                print("最新的分钟K线数据:")
                for row in result:
                    print(f"  {row.symbol} {row.timestamp}: {row.close}")


if __name__ == "__main__":
    asyncio.run(check())