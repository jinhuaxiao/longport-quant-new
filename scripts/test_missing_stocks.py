#!/usr/bin/env python3
"""测试为什么某些股票没有生成信号"""

import asyncio
from datetime import datetime, timedelta
from loguru import logger
from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient


async def test_missing_stocks():
    """测试问题股票的数据获取"""

    # 问题股票列表
    problem_stocks = [
        "9992.HK",   # 泡泡玛特
        "1024.HK",   # 快手
        "1347.HK",   # 华虹半导体
    ]

    # 对比正常工作的股票
    working_stocks = [
        "9988.HK",   # 阿里巴巴
        "0700.HK",   # 腾讯
        "1929.HK",   # 周大福
    ]

    settings = get_settings()

    async with QuoteDataClient(settings) as quote_client:
        logger.info("=" * 70)
        logger.info("测试股票数据获取问题")
        logger.info("=" * 70)

        # 测试实时行情
        logger.info("\n📊 测试实时行情获取:")
        logger.info("-" * 50)

        all_stocks = problem_stocks + working_stocks

        try:
            quotes = await quote_client.get_realtime_quote(all_stocks)

            for symbol in all_stocks:
                found = False
                for q in quotes:
                    if q.symbol == symbol:
                        price = float(q.last_done) if q.last_done else 0
                        status = "问题股票" if symbol in problem_stocks else "正常股票"
                        logger.info(f"  {symbol} ({status}): 价格=${price:.2f}, 成交量={q.volume}")
                        found = True
                        break

                if not found:
                    logger.warning(f"  {symbol}: ❌ 无实时行情数据")

        except Exception as e:
            logger.error(f"获取实时行情失败: {e}")

        # 测试历史K线数据
        logger.info("\n📈 测试历史K线数据获取:")
        logger.info("-" * 50)

        end_date = datetime.now()
        start_date = end_date - timedelta(days=100)

        for symbol in all_stocks:
            try:
                candles = await quote_client.get_history_candles(
                    symbol=symbol,
                    period=openapi.Period.Day,
                    adjust_type=openapi.AdjustType.NoAdjust,
                    start=start_date,
                    end=end_date
                )

                status = "问题股票" if symbol in problem_stocks else "正常股票"

                if candles and len(candles) > 0:
                    latest = candles[-1]
                    logger.info(
                        f"  {symbol} ({status}): "
                        f"获取到 {len(candles)} 天数据, "
                        f"最新价=${float(latest.close):.2f}, "
                        f"日期={latest.timestamp.date()}"
                    )
                else:
                    logger.warning(f"  {symbol} ({status}): ❌ 无历史数据")

            except Exception as e:
                if "301607" in str(e):
                    logger.warning(f"  {symbol}: ⚠️ API限制 - {e}")
                elif "301600" in str(e):
                    logger.warning(f"  {symbol}: ⚠️ 无权限访问此标的")
                elif "404001" in str(e):
                    logger.warning(f"  {symbol}: ⚠️ 标的不存在或代码错误")
                else:
                    logger.error(f"  {symbol}: ❌ 获取失败 - {e}")

        # 测试买卖盘深度数据
        logger.info("\n💹 测试买卖盘深度数据:")
        logger.info("-" * 50)

        for symbol in problem_stocks:
            try:
                depth = await quote_client.get_depth(symbol)

                bid_price = float(depth.bids[0].price) if depth.bids else 0
                ask_price = float(depth.asks[0].price) if depth.asks else 0

                logger.info(
                    f"  {symbol}: "
                    f"买一=${bid_price:.2f}, "
                    f"卖一=${ask_price:.2f}"
                )

            except Exception as e:
                logger.warning(f"  {symbol}: ❌ 无法获取深度数据 - {e}")

        # 分析可能的原因
        logger.info("\n" + "=" * 70)
        logger.info("📋 问题分析总结")
        logger.info("=" * 70)

        logger.info("\n可能的原因：")
        logger.info("1. API权限问题：某些股票可能需要特殊权限")
        logger.info("2. 股票代码问题：检查是否使用了正确的代码格式")
        logger.info("3. 交易状态：股票可能停牌或未上市")
        logger.info("4. 数据源问题：券商API可能暂时没有这些股票的数据")
        logger.info("5. 账户等级：某些股票可能需要更高级别的账户权限")

        logger.info("\n建议解决方案：")
        logger.info("• 检查券商账户权限设置")
        logger.info("• 确认股票代码格式（4位数字，不带前导0）")
        logger.info("• 联系券商确认这些股票是否可交易")
        logger.info("• 暂时从监控列表中移除问题股票")


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════════════╗
║                   测试特定股票无信号问题                                ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  问题描述:                                                            ║
║    运行 python scripts/advanced_technical_trading.py --builtin 时     ║
║    某些股票没有生成交易信号                                            ║
║                                                                       ║
║  问题股票:                                                            ║
║    • 9992.HK (泡泡玛特)                                              ║
║    • 1024.HK (快手)                                                  ║
║    • 1347.HK (华虹半导体)                                            ║
║                                                                       ║
║  测试内容:                                                            ║
║    1. 实时行情数据获取                                                ║
║    2. 历史K线数据获取                                                 ║
║    3. 买卖盘深度数据                                                  ║
║    4. 对比正常工作的股票                                              ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
    """)

    asyncio.run(test_missing_stocks())