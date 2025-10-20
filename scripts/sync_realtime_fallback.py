#!/usr/bin/env python3
"""当历史K线配额用尽时，使用实时数据作为后备方案"""

import asyncio
from datetime import date, datetime, timedelta
from loguru import logger

from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.persistence.models import KlineDaily
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert


async def sync_today_using_realtime(symbols: list[str]):
    """使用实时行情API同步今日数据（不占用历史K线配额）"""
    settings = get_settings()
    db = DatabaseSessionManager(settings.database_dsn, auto_init=True)
    quote_client = QuoteDataClient(settings)

    today = date.today()
    logger.info(f"使用实时行情API同步 {len(symbols)} 个股票的今日数据")

    success = 0
    failed = 0

    for symbol in symbols:
        try:
            # 使用实时行情API（不计入历史配额）
            quotes = await quote_client.get_realtime_quote([symbol])

            if not quotes or len(quotes) == 0:
                logger.warning(f"{symbol}: 无实时行情数据")
                failed += 1
                continue

            quote = quotes[0]

            # 检查是否已有今日数据
            async with db.session() as session:
                async with session.begin():
                    stmt = select(KlineDaily).where(
                        KlineDaily.symbol == symbol,
                        KlineDaily.trade_date == today
                    )
                    result = await session.execute(stmt)
                    existing = result.scalar_one_or_none()

                    if existing:
                        logger.info(f"{symbol}: 今日数据已存在")
                        success += 1
                        continue

                    # 插入今日数据
                    now = datetime.utcnow()
                    values = {
                        "symbol": symbol,
                        "trade_date": today,
                        "open": float(quote.open),
                        "high": float(quote.high),
                        "low": float(quote.low),
                        "close": float(quote.last_done),
                        "volume": int(quote.volume),
                        "turnover": float(quote.turnover),
                        "prev_close": float(quote.prev_close),
                        "change_val": float(quote.last_done - quote.prev_close),
                        "change_rate": float((quote.last_done - quote.prev_close) / quote.prev_close * 100) if quote.prev_close > 0 else 0,
                        "amplitude": float((quote.high - quote.low) / quote.prev_close * 100) if quote.prev_close > 0 else 0,
                        "turnover_rate": None,
                        "adjust_flag": 1,
                        "created_at": now,
                        "updated_at": now,
                    }

                    stmt = insert(KlineDaily).values(**values).on_conflict_do_update(
                        index_elements=["symbol", "trade_date"],
                        set_=values
                    )

                    await session.execute(stmt)

                logger.info(f"{symbol}: 同步成功 (实时数据)")
                success += 1

            # 延迟避免限流
            await asyncio.sleep(0.1)

        except Exception as e:
            logger.error(f"{symbol}: 同步失败 - {e}")
            failed += 1

    await db.close()

    logger.info("=" * 60)
    logger.info(f"实时数据同步完成！")
    logger.info(f"  成功: {success}")
    logger.info(f"  失败: {failed}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python sync_realtime_fallback.py SYMBOL1 SYMBOL2 ...")
        print("示例: python sync_realtime_fallback.py TQQQ.US NVDU.US")
        sys.exit(1)

    symbols = sys.argv[1:]
    asyncio.run(sync_today_using_realtime(symbols))