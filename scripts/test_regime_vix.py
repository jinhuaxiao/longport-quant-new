#!/usr/bin/env python3
"""测试市场状态判断（包括VIX反向指标）"""

import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from loguru import logger
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.risk.regime import RegimeClassifier


async def test_regime():
    """测试市场状态判断"""
    print("=" * 80)
    print(f"{'测试市场状态判断（支持QQQ和VIX）':^80}")
    print("=" * 80)
    print()

    # 获取配置
    settings = get_settings()

    print("配置信息：")
    print(f"  正向指标: {settings.regime_index_symbols}")
    print(f"  反向指标: {settings.regime_inverse_symbols}")
    print(f"  MA周期:   {settings.regime_ma_period}")
    print()

    # 创建行情客户端和分类器
    quote_client = QuoteDataClient(settings)
    classifier = RegimeClassifier(settings)

    print("-" * 80)
    print("测试1: 不过滤市场时段（查看所有指数）")
    print("-" * 80)

    try:
        result = await classifier.classify(quote_client, filter_by_market=False)
        print(f"\n✅ 市场状态判断结果：")
        print(f"  状态:     {result.regime}")
        print(f"  详情:     {result.details}")
        print(f"  活跃市场: {result.active_market}")
    except Exception as e:
        print(f"\n❌ 判断失败: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "-" * 80)
    print("测试2: 过滤市场时段（只看当前市场的指数）")
    print("-" * 80)

    try:
        result = await classifier.classify(quote_client, filter_by_market=True)
        print(f"\n✅ 市场状态判断结果：")
        print(f"  状态:     {result.regime}")
        print(f"  详情:     {result.details}")
        print(f"  活跃市场: {result.active_market}")
    except Exception as e:
        print(f"\n❌ 判断失败: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "-" * 80)
    print("测试3: 查询指数数据详情")
    print("-" * 80)

    # 测试正向指标（QQQ）
    try:
        from longport import openapi

        print("\n【正向指标 - QQQ.US】")
        candles = await quote_client.get_candlesticks(
            symbol="QQQ.US",
            period=openapi.Period.Day,
            count=210,
            adjust_type=openapi.AdjustType.NoAdjust,
        )
        if candles and len(candles) >= 200:
            closes = [float(c.close) for c in candles]
            last = closes[-1]
            ma200 = sum(closes[-200:]) / 200

            print(f"  最新收盘价: ${last:.2f}")
            print(f"  MA200:      ${ma200:.2f}")
            print(f"  关系:       {'收盘在MA之上 ✅' if last >= ma200 else '收盘在MA之下 ❌'}")
            print(f"  判断:       {'看涨' if last >= ma200 else '看跌'}")
        else:
            print(f"  数据不足（需要200根K线，实际{len(candles)}根）")
    except Exception as e:
        print(f"  ❌ 查询失败: {e}")

    # 测试反向指标（VIX）
    try:
        print("\n【反向指标 - ^VIX】")
        candles = await quote_client.get_candlesticks(
            symbol="^VIX",
            period=openapi.Period.Day,
            count=210,
            adjust_type=openapi.AdjustType.NoAdjust,
        )
        if candles and len(candles) >= 200:
            closes = [float(c.close) for c in candles]
            last = closes[-1]
            ma200 = sum(closes[-200:]) / 200

            print(f"  最新VIX:    {last:.2f}")
            print(f"  MA200:      {ma200:.2f}")
            print(f"  关系:       {'VIX低于MA ✅' if last < ma200 else 'VIX高于MA ❌'}")
            print(f"  判断:       {'看涨（市场平静）' if last < ma200 else '看跌（市场恐慌）'}")
        else:
            print(f"  数据不足（需要200根K线，实际{len(candles)}根）")
    except Exception as e:
        print(f"  ❌ 查询失败: {e}")

    # 测试港股指标（HSI）
    try:
        print("\n【港股指标 - HSI.HK】")
        candles = await quote_client.get_candlesticks(
            symbol="HSI.HK",
            period=openapi.Period.Day,
            count=210,
            adjust_type=openapi.AdjustType.NoAdjust,
        )
        if candles and len(candles) >= 200:
            closes = [float(c.close) for c in candles]
            last = closes[-1]
            ma200 = sum(closes[-200:]) / 200

            print(f"  最新收盘价: {last:.2f}")
            print(f"  MA200:      {ma200:.2f}")
            print(f"  关系:       {'收盘在MA之上 ✅' if last >= ma200 else '收盘在MA之下 ❌'}")
            print(f"  判断:       {'看涨' if last >= ma200 else '看跌'}")
        else:
            print(f"  数据不足（需要200根K线，实际{len(candles)}根）")
    except Exception as e:
        print(f"  ❌ 查询失败: {e}")

    print()
    print("=" * 80)
    print("测试完成！")
    print("=" * 80)


if __name__ == "__main__":
    try:
        asyncio.run(test_regime())
    except KeyboardInterrupt:
        print("\n\n❌ 测试被中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
