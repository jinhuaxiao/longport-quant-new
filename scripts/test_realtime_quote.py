#!/usr/bin/env python3
"""测试实时行情获取和推送"""

import asyncio
from time import sleep
from loguru import logger

from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient


def test_batch_quote():
    """测试批量获取实时行情（最多500个标的）"""
    logger.info("=" * 60)
    logger.info("测试批量获取实时行情")
    logger.info("=" * 60)

    config = openapi.Config.from_env()
    ctx = openapi.QuoteContext(config)

    # 测试多个标的（API支持最多500个）
    symbols = [
        "700.HK", "9988.HK", "3690.HK", "1810.HK",
        "AAPL.US", "TSLA.US", "MSFT.US", "NVDA.US"
    ]

    logger.info(f"获取 {len(symbols)} 个标的的实时行情...")

    try:
        quotes = ctx.quote(symbols)
        logger.success(f"✅ 成功获取 {len(quotes)} 个标的的行情")

        for quote in quotes:
            logger.info(
                f"  {quote.symbol}: "
                f"${quote.last_done:.2f} "
                f"({'+' if quote.prev_close and quote.last_done > quote.prev_close else ''}"
                f"{((quote.last_done - quote.prev_close) / quote.prev_close * 100):.2f}%) "
                f"Vol: {quote.volume:,}"
            )

    except Exception as e:
        logger.error(f"❌ 获取行情失败: {e}")


def test_realtime_push():
    """测试实时行情推送"""
    logger.info("\n" + "=" * 60)
    logger.info("测试实时行情推送")
    logger.info("=" * 60)

    received_count = 0

    def on_quote(symbol: str, event: openapi.PushQuote):
        nonlocal received_count
        received_count += 1
        logger.info(
            f"📬 实时推送 #{received_count}: {symbol} - "
            f"${event.last_done:.2f} "
            f"Vol: {event.volume:,} "
            f"Time: {event.timestamp}"
        )

    config = openapi.Config.from_env()
    ctx = openapi.QuoteContext(config)
    ctx.set_on_quote(on_quote)

    # 订阅行情
    symbols = ["700.HK", "AAPL.US"]
    logger.info(f"订阅 {symbols} 的实时行情推送...")

    try:
        ctx.subscribe(symbols, [openapi.SubType.Quote], is_first_push=True)
        logger.success(f"✅ 订阅成功，等待推送...")

        # 等待30秒接收推送
        logger.info("等待30秒接收实时推送数据...")
        sleep(30)

        logger.info(f"\n共收到 {received_count} 条实时推送")

    except Exception as e:
        logger.error(f"❌ 订阅失败: {e}")
    finally:
        # 取消订阅
        try:
            ctx.unsubscribe(symbols, [openapi.SubType.Quote])
            logger.info("✅ 已取消订阅")
        except:
            pass


async def test_async_quote():
    """测试异步方式获取行情"""
    logger.info("\n" + "=" * 60)
    logger.info("测试异步获取行情（使用系统封装）")
    logger.info("=" * 60)

    settings = get_settings()
    async with QuoteDataClient(settings) as client:
        symbols = ["9988.HK", "3690.HK", "AAPL.US", "MSFT.US"]

        logger.info(f"获取 {len(symbols)} 个标的的实时行情...")

        quotes = await client.get_realtime_quote(symbols)
        logger.success(f"✅ 成功获取 {len(quotes)} 个标的的行情")

        for quote in quotes:
            logger.info(
                f"  {quote.symbol}: "
                f"${quote.last_done:.2f} "
                f"({'+' if quote.prev_close and quote.last_done > quote.prev_close else ''}"
                f"{((quote.last_done - quote.prev_close) / quote.prev_close * 100):.2f}%) "
                f"Vol: {quote.volume:,}"
            )


def main():
    """主函数"""
    logger.info("\n" + "=" * 80)
    logger.info("长桥实时行情测试")
    logger.info("=" * 80)

    # 1. 测试批量获取行情
    test_batch_quote()

    # 2. 测试异步获取
    asyncio.run(test_async_quote())

    # 3. 测试实时推送（需要在交易时段才有推送）
    # test_realtime_push()

    logger.info("\n" + "=" * 80)
    logger.info("测试完成")
    logger.info("=" * 80)
    logger.info("\n提示:")
    logger.info("1. 批量获取最多支持500个标的")
    logger.info("2. 实时推送需要订阅且在交易时段才有数据")
    logger.info("3. 港股BMP基础报价无实时推送，需要升级行情权限")
    logger.info("4. 美股LV1有实时推送")


if __name__ == "__main__":
    main()