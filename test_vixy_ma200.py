#!/usr/bin/env python3
"""
测试VIXY MA200功能是否正常
验证get_candlesticks的adjust_type参数问题已修复
"""

import asyncio
from longport.openapi import QuoteContext, Config, Period, AdjustType
from datetime import datetime
import os


async def test_vixy_ma200():
    """测试VIXY MA200获取功能"""

    print("\n" + "="*60)
    print("📊 VIXY MA200 功能测试")
    print("="*60)

    # 初始化配置
    config = Config.from_env()
    ctx = QuoteContext(config)

    try:
        # 测试获取VIXY K线数据
        print("\n1️⃣ 测试获取VIXY K线数据...")
        vixy_symbol = "VIXY.US"

        # 获取200日K线
        bars = ctx.candlesticks(
            symbol=vixy_symbol,
            period=Period.Day,
            count=200,
            adjust_type=AdjustType.NoAdjust
        )

        if bars:
            print(f"   ✅ 成功获取 {len(bars)} 条K线数据")

            # 计算MA200
            if len(bars) >= 200:
                closes = [float(bar.close) for bar in bars[-200:]]
                ma200 = sum(closes) / len(closes)

                print(f"\n2️⃣ MA200计算:")
                print(f"   • MA200 = ${ma200:.2f}")
                print(f"   • 当前价 = ${float(bars[-1].close):.2f}")
                print(f"   • 相对MA200 = {float(bars[-1].close)/ma200*100:.1f}%")

                # 显示最近5日数据
                print(f"\n3️⃣ 最近5日VIXY数据:")
                for i in range(-5, 0):
                    bar = bars[i]
                    print(f"   {bar.timestamp}: Close=${float(bar.close):.2f}, "
                          f"High=${float(bar.high):.2f}, Low=${float(bar.low):.2f}")

                # 判断趋势
                print(f"\n4️⃣ 趋势分析:")
                current = float(bars[-1].close)
                ma20 = sum(float(b.close) for b in bars[-20:]) / 20
                ma50 = sum(float(b.close) for b in bars[-50:]) / 50

                print(f"   • MA20  = ${ma20:.2f} ({current/ma20*100:.1f}%)")
                print(f"   • MA50  = ${ma50:.2f} ({current/ma50*100:.1f}%)")
                print(f"   • MA200 = ${ma200:.2f} ({current/ma200*100:.1f}%)")

                if current > ma200 * 1.1:
                    print(f"   ⚠️ VIXY高于MA200 10%以上，市场可能处于恐慌")
                elif current > ma200:
                    print(f"   📈 VIXY高于MA200，波动性偏高")
                else:
                    print(f"   ✅ VIXY低于MA200，市场相对平静")

            else:
                print(f"   ⚠️ 数据不足200条，仅有{len(bars)}条")

        else:
            print("   ❌ 无法获取K线数据")

    except TypeError as e:
        if "missing 1 required positional argument: 'adjust_type'" in str(e):
            print(f"   ❌ adjust_type参数错误: {e}")
            print("   ⚠️ 请确保已更新代码并重启服务")
        else:
            print(f"   ❌ 类型错误: {e}")

    except Exception as e:
        print(f"   ❌ 获取数据失败: {e}")

    print("\n" + "="*60)
    print("✅ 测试完成")
    print("="*60)


async def main():
    """主函数"""
    await test_vixy_ma200()


if __name__ == "__main__":
    asyncio.run(main())