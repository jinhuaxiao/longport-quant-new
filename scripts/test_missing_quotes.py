#!/usr/bin/env python3
"""测试为什么部分持仓股票没有实时行情"""

import asyncio
from loguru import logger
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient

async def test_quotes():
    """测试获取股票行情"""

    # 你的持仓股票列表
    position_stocks = [
        "981.HK",    # 应该是 0981.HK (中芯国际)
        "9660.HK",   # 地平线机器人
        "883.HK",    # 应该是 0883.HK (中国海洋石油)
        "857.HK",    # 应该是 0857.HK (中国石油)
        "5.HK",      # 应该是 0005.HK (汇丰控股)
        "2319.HK",   # 蒙牛乳业
        "1929.HK",   # 周大福
        "1398.HK",   # 工商银行
        "1347.HK",   # 华虹半导体
    ]

    # 正确的股票代码格式（港股需要4位数）
    corrected_stocks = [
        "0981.HK",   # 中芯国际
        "9660.HK",   # 地平线机器人
        "0883.HK",   # 中国海洋石油
        "0857.HK",   # 中国石油
        "0005.HK",   # 汇丰控股
        "2319.HK",   # 蒙牛乳业
        "1929.HK",   # 周大福
        "1398.HK",   # 工商银行
        "1347.HK",   # 华虹半导体
    ]

    settings = get_settings()
    quote_client = QuoteDataClient(settings)

    logger.info("=" * 60)
    logger.info("测试1: 使用你当前看到的股票代码格式")
    logger.info("=" * 60)

    for symbol in position_stocks:
        try:
            quotes = await quote_client.get_realtime_quote([symbol])
            if quotes and len(quotes) > 0:
                q = quotes[0]
                price = float(q.last_done) if q.last_done else 0
                logger.success(f"✅ {symbol}: 获取到价格 ${price:.2f}")
            else:
                logger.warning(f"❌ {symbol}: 没有获取到行情数据")
        except Exception as e:
            logger.error(f"❌ {symbol}: 获取失败 - {e}")

    logger.info("\n" + "=" * 60)
    logger.info("测试2: 使用正确的4位数股票代码格式")
    logger.info("=" * 60)

    for symbol in corrected_stocks:
        try:
            quotes = await quote_client.get_realtime_quote([symbol])
            if quotes and len(quotes) > 0:
                q = quotes[0]
                price = float(q.last_done) if q.last_done else 0
                logger.success(f"✅ {symbol}: 获取到价格 ${price:.2f}")
            else:
                logger.warning(f"❌ {symbol}: 没有获取到行情数据")
        except Exception as e:
            logger.error(f"❌ {symbol}: 获取失败 - {e}")

    logger.info("\n" + "=" * 60)
    logger.info("测试3: 批量获取所有股票")
    logger.info("=" * 60)

    try:
        # 测试原始格式批量获取
        logger.info("\n原始格式批量获取:")
        quotes = await quote_client.get_realtime_quote(position_stocks)
        logger.info(f"获取到 {len(quotes)} 个股票的行情")
        for q in quotes:
            price = float(q.last_done) if q.last_done else 0
            logger.info(f"  {q.symbol}: ${price:.2f}")

        # 测试正确格式批量获取
        logger.info("\n正确格式批量获取:")
        quotes = await quote_client.get_realtime_quote(corrected_stocks)
        logger.info(f"获取到 {len(quotes)} 个股票的行情")
        for q in quotes:
            price = float(q.last_done) if q.last_done else 0
            logger.info(f"  {q.symbol}: ${price:.2f}")

    except Exception as e:
        logger.error(f"批量获取失败: {e}")

    logger.info("\n" + "=" * 60)
    logger.info("分析结论:")
    logger.info("=" * 60)
    logger.info("问题原因: 港股股票代码必须是4位数字，需要在前面补0")
    logger.info("例如:")
    logger.info("  - 5.HK    → 0005.HK (汇丰控股)")
    logger.info("  - 981.HK  → 0981.HK (中芯国际)")
    logger.info("  - 883.HK  → 0883.HK (中国海洋石油)")
    logger.info("  - 857.HK  → 0857.HK (中国石油)")
    logger.info("\n解决方案:")
    logger.info("1. 账户持仓返回的股票代码可能缺少前导0")
    logger.info("2. 需要在获取行情前对股票代码进行标准化处理")
    logger.info("3. 建议修改脚本，自动将3位数的港股代码补齐为4位")

if __name__ == "__main__":
    asyncio.run(test_quotes())