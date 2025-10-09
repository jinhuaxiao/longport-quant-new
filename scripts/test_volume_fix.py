#!/usr/bin/env python3
"""测试成交量计算修复"""

import asyncio
from datetime import datetime, timedelta
from loguru import logger
from longport import openapi
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient


async def test_volume_calculation():
    """测试成交量计算"""

    # 测试股票
    test_symbol = "9660.HK"  # 地平线机器人

    settings = get_settings()

    async with QuoteDataClient(settings) as quote_client:
        logger.info("=" * 70)
        logger.info(f"测试成交量计算 - {test_symbol}")
        logger.info("=" * 70)

        # 获取实时行情
        try:
            quotes = await quote_client.get_realtime_quote([test_symbol])
            if quotes:
                quote = quotes[0]
                current_volume = quote.volume
                logger.info(f"\n实时数据:")
                logger.info(f"  当前累计成交量: {current_volume:,}")
                logger.info(f"  当前价格: ${float(quote.last_done):.2f}")
        except Exception as e:
            logger.error(f"获取实时行情失败: {e}")
            return

        # 获取历史K线数据
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)

        try:
            candles = await quote_client.get_history_candles(
                symbol=test_symbol,
                period=openapi.Period.Day,
                adjust_type=openapi.AdjustType.NoAdjust,
                start=start_date,
                end=end_date
            )

            if candles and len(candles) > 20:
                # 提取最近20天的成交量
                volumes = [c.volume for c in candles[-21:-1]]  # 不包括今天
                avg_volume = sum(volumes) / len(volumes)

                logger.info(f"\n历史数据 (最近20天):")
                logger.info(f"  平均日成交量: {avg_volume:,.0f}")
                logger.info(f"  最高日成交量: {max(volumes):,}")
                logger.info(f"  最低日成交量: {min(volumes):,}")

                # 计算今天的成交量比率
                if avg_volume > 0:
                    volume_ratio = current_volume / avg_volume
                    logger.info(f"\n成交量分析:")
                    logger.info(f"  当前成交量比率: {volume_ratio:.2f}x")

                    if volume_ratio > 1.5:
                        logger.info(f"  状态: 放量 (>1.5x)")
                    elif volume_ratio < 0.5:
                        logger.info(f"  状态: 缩量 (<0.5x)")
                    else:
                        logger.info(f"  状态: 正常")

                    # 显示最近几天的成交量对比
                    logger.info(f"\n最近5天成交量:")
                    for i, candle in enumerate(candles[-5:], 1):
                        ratio = candle.volume / avg_volume
                        date_str = candle.timestamp.strftime('%Y-%m-%d')
                        logger.info(f"  {date_str}: {candle.volume:,} ({ratio:.2f}x)")

        except Exception as e:
            logger.error(f"获取历史数据失败: {e}")

        logger.info("\n" + "=" * 70)
        logger.info("成交量计算说明")
        logger.info("=" * 70)
        logger.info("问题原因:")
        logger.info("  • quote.volume 是今日累计成交量（从开盘到现在）")
        logger.info("  • 历史volume_sma 是完整交易日的平均成交量")
        logger.info("  • 盘中比较会导致比率偏低")
        logger.info("\n解决方案:")
        logger.info("  • 正确处理成交量数据类型转换")
        logger.info("  • 添加调试日志显示具体数值")
        logger.info("  • 考虑时间因素调整比率计算")


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════════════╗
║                     测试成交量计算修复                                  ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  问题描述:                                                            ║
║    9660.HK等股票显示成交量0.00x，导致成交量得分为0                       ║
║                                                                       ║
║  修复内容:                                                            ║
║    1. 修正成交量比率计算逻辑                                          ║
║    2. 处理数据类型转换问题                                            ║
║    3. 添加调试日志                                                   ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
    """)

    asyncio.run(test_volume_calculation())