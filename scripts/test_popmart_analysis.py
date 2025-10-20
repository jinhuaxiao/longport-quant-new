#!/usr/bin/env python3
"""测试泡泡玛特（9992.HK）的分析流程"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.append(str(Path(__file__).parent.parent))

from longport import openapi
from loguru import logger
from src.longport_quant.config import get_settings
from src.longport_quant.data.quote_client import QuoteDataClient
import numpy as np

# 设置日志级别为DEBUG以查看所有详细信息
logger.remove()
logger.add(sys.stdout, level="DEBUG")


async def test_popmart():
    """测试泡泡玛特分析"""
    symbol = "9992.HK"
    settings = get_settings()

    logger.info("=" * 70)
    logger.info(f"测试标的: {symbol} (泡泡玛特)")
    logger.info("=" * 70)

    async with QuoteDataClient(settings) as quote_client:
        # 步骤1: 获取实时行情
        logger.info("\n[步骤1] 获取实时行情...")
        try:
            quotes = await quote_client.get_realtime_quote([symbol])
            if not quotes:
                logger.error("❌ 无法获取实时行情")
                return

            quote = quotes[0]
            current_price = float(quote.last_done)
            logger.success(f"✅ 实时行情: 价格=${current_price:.2f}, 成交量={quote.volume:,}")
        except Exception as e:
            logger.error(f"❌ 获取实时行情失败: {type(e).__name__}: {e}")
            return

        # 步骤2: 获取历史K线数据
        logger.info("\n[步骤2] 获取历史K线数据...")
        end_date = datetime.now()
        days_to_fetch = 100
        start_date = end_date - timedelta(days=days_to_fetch)

        logger.debug(f"  请求参数:")
        logger.debug(f"    symbol: {symbol}")
        logger.debug(f"    period: Day")
        logger.debug(f"    start: {start_date.date()}")
        logger.debug(f"    end: {end_date.date()}")
        logger.debug(f"    天数: {days_to_fetch}天")

        try:
            candles = await quote_client.get_history_candles(
                symbol=symbol,
                period=openapi.Period.Day,
                adjust_type=openapi.AdjustType.NoAdjust,
                start=start_date,
                end=end_date
            )

            if not candles:
                logger.error("❌ 返回的K线数据为空")
                return

            logger.success(f"✅ 获取到 {len(candles)} 天K线数据")

            # 显示前3条和后3条数据
            logger.debug(f"\n  前3条数据:")
            for i, c in enumerate(candles[:3]):
                logger.debug(f"    [{i}] 日期: {c.timestamp}, 收盘: ${c.close}, 成交量: {c.volume}")

            logger.debug(f"\n  后3条数据:")
            for i, c in enumerate(candles[-3:]):
                logger.debug(f"    [{len(candles)-3+i}] 日期: {c.timestamp}, 收盘: ${c.close}, 成交量: {c.volume}")

        except Exception as e:
            logger.error(f"❌ 获取K线数据失败:")
            logger.error(f"  错误类型: {type(e).__name__}")
            logger.error(f"  错误信息: {e}")

            # 检查特定的错误码
            error_msg = str(e)
            if "301607" in error_msg:
                logger.warning("  → 原因: API请求频率过高")
            elif "301600" in error_msg:
                logger.warning("  → 原因: 无权限访问")
            elif "404001" in error_msg:
                logger.warning("  → 原因: 标的不存在或代码错误")
            elif "timeout" in error_msg.lower():
                logger.warning("  → 原因: 请求超时")

            import traceback
            logger.debug(f"\n  完整堆栈跟踪:\n{traceback.format_exc()}")
            return

        # 步骤3: 检查数据是否足够
        logger.info("\n[步骤3] 检查数据充足性...")
        min_required = 30

        if len(candles) < min_required:
            logger.warning(f"⚠️ 数据不足:")
            logger.warning(f"  需要: 至少 {min_required} 天")
            logger.warning(f"  实际: {len(candles)} 天")
            logger.warning(f"  差距: 缺少 {min_required - len(candles)} 天")
            return
        else:
            logger.success(f"✅ 数据充足: {len(candles)} 天 >= {min_required} 天")

        # 步骤4: 提取并验证数据
        logger.info("\n[步骤4] 提取并验证数据...")
        try:
            closes = np.array([float(c.close) for c in candles])
            highs = np.array([float(c.high) for c in candles])
            lows = np.array([float(c.low) for c in candles])
            volumes = np.array([c.volume for c in candles])

            logger.success(f"✅ 数据提取成功:")
            logger.info(f"  closes: {len(closes)} 个数据点, 范围 ${closes.min():.2f} - ${closes.max():.2f}")
            logger.info(f"  highs: {len(highs)} 个数据点, 范围 ${highs.min():.2f} - ${highs.max():.2f}")
            logger.info(f"  lows: {len(lows)} 个数据点, 范围 ${lows.min():.2f} - ${lows.max():.2f}")
            logger.info(f"  volumes: {len(volumes)} 个数据点, 范围 {volumes.min():,} - {volumes.max():,}")

            # 检查是否有NaN或无效值
            if np.any(np.isnan(closes)):
                logger.warning(f"⚠️ closes 中有 NaN 值")
            if np.any(np.isnan(highs)):
                logger.warning(f"⚠️ highs 中有 NaN 值")
            if np.any(np.isnan(lows)):
                logger.warning(f"⚠️ lows 中有 NaN 值")
            if np.any(np.isnan(volumes)):
                logger.warning(f"⚠️ volumes 中有 NaN 值")

        except Exception as e:
            logger.error(f"❌ 数据提取失败: {type(e).__name__}: {e}")
            return

        # 步骤5: 计算技术指标（简化版测试）
        logger.info("\n[步骤5] 测试技术指标计算...")
        try:
            from src.longport_quant.features.technical_indicators import TechnicalIndicators

            # RSI
            logger.debug("  计算 RSI...")
            rsi = TechnicalIndicators.rsi(closes, 14)
            logger.success(f"  ✅ RSI: {rsi[-1]:.2f}")

            # 布林带
            logger.debug("  计算 布林带...")
            bb = TechnicalIndicators.bollinger_bands(closes, 20, 2)
            logger.success(f"  ✅ 布林带: 上轨=${bb['upper'][-1]:.2f}, 中轨=${bb['middle'][-1]:.2f}, 下轨=${bb['lower'][-1]:.2f}")

            # MACD
            logger.debug("  计算 MACD...")
            macd = TechnicalIndicators.macd(closes, 12, 26, 9)
            logger.success(f"  ✅ MACD: {macd['macd'][-1]:.3f} vs 信号线{macd['signal'][-1]:.3f}")

            # 成交量均线
            logger.debug("  计算 成交量均线...")
            volume_sma = TechnicalIndicators.sma(volumes, 20)
            logger.success(f"  ✅ 成交量均线: {volume_sma[-1]:,.0f}")

            # ATR
            logger.debug("  计算 ATR...")
            atr = TechnicalIndicators.atr(highs, lows, closes, 14)
            logger.success(f"  ✅ ATR: {atr[-1]:.2f}")

            logger.success("\n✅ 所有技术指标计算成功！")

        except Exception as e:
            logger.error(f"❌ 技术指标计算失败:")
            logger.error(f"  错误类型: {type(e).__name__}")
            logger.error(f"  错误信息: {e}")

            import traceback
            logger.debug(f"\n  完整堆栈跟踪:\n{traceback.format_exc()}")
            return

        # 总结
        logger.info("\n" + "=" * 70)
        logger.success("🎉 测试完成！所有步骤都成功执行")
        logger.info("=" * 70)
        logger.info("\n结论: 泡泡玛特的数据和分析逻辑都正常，")
        logger.info("      如果主程序中没有显示分析结果，")
        logger.info("      很可能是异常被捕获后静默处理了。")
        logger.info("      现在修复后的日志应该能显示详细信息。")


async def main():
    try:
        await test_popmart()
    except Exception as e:
        logger.error(f"测试脚本执行失败: {e}")
        import traceback
        logger.debug(traceback.format_exc())


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════╗
║         泡泡玛特 (9992.HK) 分析测试                           ║
╠══════════════════════════════════════════════════════════════╣
║  测试内容:                                                    ║
║  1. 获取实时行情                                             ║
║  2. 获取历史K线数据                                          ║
║  3. 检查数据充足性                                           ║
║  4. 验证数据有效性                                           ║
║  5. 计算技术指标                                             ║
╚══════════════════════════════════════════════════════════════╝
    """)
    asyncio.run(main())
