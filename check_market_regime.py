#!/usr/bin/env python3
"""检查当前市场状态（QQQ/SPY MA200）"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).parent))

from longport import openapi
from longport_quant.config import get_settings


async def check_market_regime():
    """检查市场状态"""
    settings = get_settings(account_id="paper_001")

    # 创建行情客户端
    config = openapi.Config(
        app_key=settings.longport_app_key,
        app_secret=settings.longport_app_secret,
        access_token=settings.longport_access_token,
    )

    client = openapi.QuoteContext(config)

    print("\n" + "="*70)
    print("📊 美股市场状态检查（QQQ/SPY vs MA200）")
    print("="*70)

    symbols = ["QQQ.US", "SPY.US"]

    for symbol in symbols:
        try:
            # 获取实时行情
            quotes = client.quote([symbol])
            current_price = float(quotes[0].last_done) if quotes else None

            # 获取历史数据计算 MA200
            bars = client.candlesticks(
                symbol,
                openapi.Period.Day,
                count=200,
                adjust_type=openapi.AdjustType.NoAdjust
            )

            if bars and len(bars) >= 200:
                closes = [float(bar.close) for bar in bars[-200:]]
                ma200 = sum(closes) / len(closes)

                print(f"\n📈 {symbol}:")
                print(f"   当前价格: ${current_price:.2f}")
                print(f"   MA200: ${ma200:.2f}")
                print(f"   偏离率: {((current_price / ma200 - 1) * 100):.2f}%")

                if current_price > ma200:
                    print(f"   🐂 状态: 牛市（价格在 MA200 上方）")
                else:
                    print(f"   🐻 状态: 熊市（价格在 MA200 下方）")
            else:
                print(f"\n❌ {symbol}: 历史数据不足")

        except Exception as e:
            print(f"\n❌ {symbol} 检查失败: {e}")

    # 检查 HSI.HK
    try:
        symbol = "HSI.HK"
        quotes = client.quote([symbol])
        current_price = float(quotes[0].last_done) if quotes else None

        bars = client.candlesticks(
            symbol,
            openapi.Period.Day,
            count=200,
            adjust_type=openapi.AdjustType.NoAdjust
        )

        if bars and len(bars) >= 200:
            closes = [float(bar.close) for bar in bars[-200:]]
            ma200 = sum(closes) / len(closes)

            print(f"\n📈 {symbol}:")
            print(f"   当前价格: ${current_price:.2f}")
            print(f"   MA200: ${ma200:.2f}")
            print(f"   偏离率: {((current_price / ma200 - 1) * 100):.2f}%")

            if current_price > ma200:
                print(f"   🐂 状态: 牛市（价格在 MA200 上方）")
            else:
                print(f"   🐻 状态: 熊市（价格在 MA200 下方）")
    except Exception as e:
        print(f"\n❌ HSI.HK 检查失败: {e}")

    # 综合判断
    print("\n" + "="*70)
    print("💡 市场状态判断:")
    print("   - 3个指数都 > MA200 → 🐂 牛市（保留15%现金，正常交易）")
    print("   - 部分指数 > MA200 → 📊 震荡（保留30%现金，减少30%仓位）")
    print("   - 所有指数 < MA200 → 🐻 熊市（保留50%现金，减少60%仓位）")
    print("="*70)


if __name__ == "__main__":
    asyncio.run(check_market_regime())
