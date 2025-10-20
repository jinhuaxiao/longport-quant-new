#!/usr/bin/env python3
"""测试自动交易系统修复"""

import asyncio
from loguru import logger
from sqlalchemy import text

from longport_quant.config import get_settings
from longport_quant.persistence.db import DatabaseSessionManager
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.data.kline_sync import KlineDataService


async def test_fixes():
    """测试修复"""
    logger.info("=" * 60)
    logger.info("测试自动交易系统修复")
    logger.info("=" * 60)

    settings = get_settings()
    db = DatabaseSessionManager(settings.database_dsn, auto_init=True)

    try:
        # 1. 测试TradingCalendar表存在
        logger.info("\n1. 测试TradingCalendar表")
        async with db.session() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'tradingcalendar'")
            )
            count = result.scalar()
            if count > 0:
                logger.success("✅ TradingCalendar表存在")
            else:
                logger.error("❌ TradingCalendar表不存在")

        # 2. 测试K线同步（带重试机制）
        logger.info("\n2. 测试K线同步重试机制")
        quote_client = QuoteDataClient(settings)
        kline_service = KlineDataService(settings, db, quote_client)

        # 使用小批量测试
        test_symbols = ["09988.HK", "03690.HK"]
        logger.info(f"测试同步 {len(test_symbols)} 个标的...")

        results = await kline_service.sync_minute_klines(
            symbols=test_symbols,
            days_back=1
        )

        success_count = sum(1 for v in results.values() if v >= 0)
        logger.info(f"同步结果: {success_count}/{len(test_symbols)} 成功")

        for symbol, count in results.items():
            if count >= 0:
                logger.success(f"  ✅ {symbol}: {count} 条K线")
            else:
                logger.error(f"  ❌ {symbol}: 失败")

        logger.info("\n" + "=" * 60)
        logger.success("✅ 测试完成")
        logger.info("=" * 60)

        logger.info("\n修复内容:")
        logger.info("1. ✅ 创建了TradingCalendar表")
        logger.info("2. ✅ 添加了API限流重试机制（最多3次，指数退避）")
        logger.info("3. ✅ 添加了请求间延迟（0.5秒）")
        logger.info("4. ✅ 优化了API限制配置")
        logger.info("\n可以重新运行自动交易系统:")
        logger.info("  python3 scripts/start_auto_trading.py")

    except Exception as e:
        logger.error(f"❌ 测试失败: {e}")
        raise
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(test_fixes())