#!/usr/bin/env python3
"""调试行情API"""

import asyncio
from longport import openapi
from longport_quant.config import get_settings
from loguru import logger


async def debug_api():
    """调试API"""
    settings = get_settings()

    logger.info("=" * 60)
    logger.info("调试长桥行情API")
    logger.info("=" * 60)

    # 测试不同的标的
    test_symbols = [
        ["09988.HK"],  # 单个港股
        ["AAPL.US"],   # 单个美股
        ["09988.HK", "AAPL.US"],  # 混合
        ["00700.HK"],  # 腾讯
    ]

    config = openapi.Config.from_env()
    logger.info(f"\n配置信息: 已加载")

    ctx = openapi.QuoteContext(config)

    for symbols in test_symbols:
        logger.info(f"\n{'='*60}")
        logger.info(f"测试标的: {symbols}")
        logger.info(f"{'='*60}")

        try:
            # 方法1: realtime_quote
            logger.info("\n1. 测试 realtime_quote():")
            quotes = ctx.realtime_quote(symbols)
            logger.info(f"   返回数量: {len(quotes)}")

            if quotes:
                for quote in quotes:
                    logger.info(f"   ✅ {quote.symbol}: last_done={quote.last_done}, volume={quote.volume}")
            else:
                logger.warning("   ⚠️  返回空列表")

            # 方法2: quote
            logger.info("\n2. 测试 quote():")
            for symbol in symbols:
                try:
                    quote = ctx.quote([symbol])
                    logger.info(f"   quote({symbol}): {len(quote)} 个结果")
                    if quote:
                        q = quote[0]
                        logger.info(f"     last_done={q.last_done}, prev_close={q.prev_close}")
                except Exception as e:
                    logger.error(f"   ❌ quote({symbol}) 失败: {e}")

            # 方法3: static_info
            logger.info("\n3. 测试 static_info():")
            try:
                info = ctx.static_info(symbols)
                logger.info(f"   返回数量: {len(info)}")
                if info:
                    for item in info:
                        logger.info(f"   ✅ {item.symbol}: name={item.name_cn}, market={item.market}")
            except Exception as e:
                logger.error(f"   ❌ static_info() 失败: {e}")

        except Exception as e:
            logger.error(f"❌ 测试失败: {e}")
            import traceback
            traceback.print_exc()

    logger.info("\n" + "=" * 60)
    logger.info("测试完成")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(debug_api())