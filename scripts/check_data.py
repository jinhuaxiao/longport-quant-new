#!/usr/bin/env python3
"""检查数据库中的数据"""
import asyncio
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.config import get_settings
from sqlalchemy import text


async def check():
    settings = get_settings()
    async with DatabaseSessionManager(settings.database_dsn) as db:
        async with db.session() as session:
            result = await session.execute(text('SELECT COUNT(*) FROM security_universe'))
            count = result.scalar()
            print(f'security_universe表中有 {count} 条记录')

            result = await session.execute(text('SELECT COUNT(*) FROM security_static'))
            count = result.scalar()
            print(f'security_static表中有 {count} 条记录')

            # 显示一些样例数据
            result = await session.execute(
                text('SELECT symbol, name_cn, name_en FROM security_universe LIMIT 5')
            )
            print("\n样例数据:")
            for row in result:
                print(f"  {row.symbol}: {row.name_cn or row.name_en}")

            # 检查K线数据
            result = await session.execute(text('SELECT COUNT(*) FROM kline_daily'))
            count = result.scalar()
            print(f"\nkline_daily表中有 {count} 条记录")

            if count > 0:
                result = await session.execute(
                    text('SELECT symbol, trade_date, close FROM kline_daily ORDER BY trade_date DESC LIMIT 5')
                )
                print("最新的K线数据:")
                for row in result:
                    print(f"  {row.symbol} {row.trade_date}: {row.close}")


if __name__ == "__main__":
    asyncio.run(check())