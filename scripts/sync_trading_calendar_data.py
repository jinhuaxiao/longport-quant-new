#!/usr/bin/env python3
"""同步交易日历数据到数据库"""

import asyncio
from datetime import date, datetime, timedelta
from loguru import logger

from longport_quant.config import get_settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import TradingCalendar
from longport_quant.data.quote_client import QuoteDataClient
from longport import openapi


async def sync_trading_calendar():
    """同步交易日历数据"""
    logger.info("=" * 60)
    logger.info("同步交易日历数据")
    logger.info("=" * 60)

    settings = get_settings()
    db = DatabaseSessionManager(settings.database_dsn, auto_init=True)

    try:
        async with QuoteDataClient(settings) as quote_client:
            # API限制：最多查询30天，所以分批查询
            # 查询过去15天和未来15天（总共30天）
            start_date = date.today() - timedelta(days=15)
            end_date = date.today() + timedelta(days=15)

            logger.info(f"查询交易日历: {start_date} 到 {end_date} (共30天)")

            # 查询港股交易日
            hk_trading_days = await quote_client.get_trading_days(
                market=openapi.Market.HK,
                begin=start_date,
                end=end_date
            )

            logger.info(f"获取到 {len(hk_trading_days.trading_days)} 个港股交易日")

            # 插入数据库
            async with db.session() as session:
                from sqlalchemy.dialects.postgresql import insert

                count = 0
                inserted = 0

                # 批量插入
                for trade_date in hk_trading_days.trading_days:
                    # 使用SQLAlchemy的insert...on_conflict
                    stmt = insert(TradingCalendar).values(
                        market="HK",
                        trade_date=trade_date,
                        sessions=[
                            {"start": "09:30", "end": "12:00"},
                            {"start": "13:00", "end": "16:00"}
                        ],
                        is_half_day=False,
                        source="longport_api"
                    ).on_conflict_do_nothing(
                        index_elements=['market', 'trade_date']
                    )

                    result = await session.execute(stmt)
                    count += 1
                    # rowcount表示实际插入的行数（不包括冲突跳过的）
                    if result.rowcount > 0:
                        inserted += 1

                await session.commit()
                logger.success(f"✅ 处理了 {count} 个交易日，新插入 {inserted} 条记录")

            # 验证数据
            async with db.session() as session:
                from sqlalchemy import select, func, text
                result = await session.execute(
                    select(func.count()).select_from(TradingCalendar)
                )
                total = result.scalar()
                logger.info(f"数据库中共有 {total} 条交易日历记录")

                # 显示最近的几个交易日
                result = await session.execute(
                    text("SELECT trade_date FROM tradingcalendar WHERE market = 'HK' ORDER BY trade_date DESC LIMIT 5")
                )
                recent_days = result.fetchall()
                logger.info("最近的交易日:")
                for row in recent_days:
                    logger.info(f"  - {row[0]}")

        logger.info("\n" + "=" * 60)
        logger.success("✅ 交易日历同步完成")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"❌ 同步失败: {e}")
        raise
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(sync_trading_calendar())