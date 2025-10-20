#!/usr/bin/env python3
"""创建TradingCalendar数据库表"""

import asyncio
import sys
sys.path.insert(0, 'src')

from loguru import logger
from longport_quant.config import get_settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import TradingCalendar


async def create_trading_calendar_table():
    """创建TradingCalendar表"""
    logger.info("正在创建TradingCalendar表...")

    settings = get_settings()
    db = DatabaseSessionManager(settings.database_dsn, auto_init=True)

    try:
        # 直接访问内部引擎
        if not db._engine:
            raise RuntimeError("数据库引擎未初始化")

        async with db._engine.begin() as conn:
            # 创建TradingCalendar表
            await conn.run_sync(TradingCalendar.__table__.create, checkfirst=True)
            logger.success("✅ TradingCalendar表创建成功")

        logger.info("表结构:")
        logger.info(f"  - id: Integer (主键)")
        logger.info(f"  - market: String(8) (市场代码)")
        logger.info(f"  - trade_date: Date (交易日期)")
        logger.info(f"  - sessions: JSONB (交易时段)")
        logger.info(f"  - is_half_day: Boolean (是否半日)")
        logger.info(f"  - source: String(32) (数据源)")
        logger.info(f"  - created_at: DateTime (创建时间)")
        logger.info(f"  - updated_at: DateTime (更新时间)")

    except Exception as e:
        logger.error(f"❌ 创建表失败: {e}")
        raise
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(create_trading_calendar_table())