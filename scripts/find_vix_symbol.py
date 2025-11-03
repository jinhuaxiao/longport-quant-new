#!/usr/bin/env python3
"""查找VIX恐慌指数的正确符号"""

import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport import openapi


async def test_vix_symbols():
    """测试不同的VIX符号格式"""
    print("=" * 80)
    print(f"{'查找VIX恐慌指数的正确符号':^80}")
    print("=" * 80)
    print()

    settings = get_settings()
    quote_client = QuoteDataClient(settings)

    # 可能的VIX符号格式
    possible_symbols = [
        "^VIX",
        "VIX",
        "VIX.US",
        "VVIX.US",
        "$VIX",
        "VIX.CBOE",
    ]

    print("测试以下VIX符号格式：\n")

    for symbol in possible_symbols:
        print(f"尝试: {symbol:15s} ... ", end="", flush=True)
        try:
            candles = await quote_client.get_candlesticks(
                symbol=symbol,
                period=openapi.Period.Day,
                count=10,
                adjust_type=openapi.AdjustType.NoAdjust,
            )
            if candles and len(candles) > 0:
                last_close = float(candles[-1].close)
                print(f"✅ 成功！最新值: {last_close:.2f}")
            else:
                print("❌ 无数据")
        except Exception as e:
            error_msg = str(e)
            if "invalid symbol" in error_msg.lower():
                print("❌ 符号无效")
            elif "not authorized" in error_msg.lower() or "no access" in error_msg.lower():
                print("⚠️  符号有效但需要订阅权限")
            else:
                print(f"❌ 错误: {error_msg[:50]}")

    print()
    print("=" * 80)
    print("建议：")
    print("  1. 如果没有找到有效的VIX符号，建议暂不使用VIX")
    print("  2. 可以只用QQQ作为美股市场状态指标")
    print("  3. 或者添加SPY、DIA等其他指数增加稳定性")
    print("=" * 80)


if __name__ == "__main__":
    try:
        asyncio.run(test_vix_symbols())
    except KeyboardInterrupt:
        print("\n\n❌ 测试被中断")
        sys.exit(1)
