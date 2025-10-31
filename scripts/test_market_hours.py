#!/usr/bin/env python3
"""测试市场交易时间判断功能"""

from datetime import datetime
from zoneinfo import ZoneInfo

from longport_quant.utils.market_hours import MarketHours


def test_market_hours():
    """测试市场时间判断"""
    print("=" * 60)
    print("市场交易时间判断测试")
    print("=" * 60)

    # 当前时间
    now_hk = datetime.now(ZoneInfo("Asia/Hong_Kong"))
    now_us = datetime.now(ZoneInfo("America/New_York"))

    print(f"\n当前时间:")
    print(f"  香港时间: {now_hk.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  美东时间: {now_us.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    # 检测当前市场
    current_market = MarketHours.get_current_market()
    market_name = MarketHours.get_market_name(current_market)

    print(f"\n当前活跃市场: {market_name} ({current_market})")

    # 测试指数过滤
    all_symbols = "HSI.HK,SPY.US,QQQ.US"
    active_symbols = MarketHours.get_active_index_symbols(all_symbols)

    print(f"\n配置的所有指数: {all_symbols}")
    print(f"当前活跃指数: {active_symbols if active_symbols else '无（非交易时段）'}")

    # 港股交易时间说明
    print(f"\n港股交易时间 (香港时间 UTC+8):")
    print(f"  上午: 09:30-12:00")
    print(f"  下午: 13:00-16:00")

    # 美股交易时间说明
    print(f"\n美股交易时间 (美东时间 UTC-5/-4):")
    print(f"  交易时段: 09:30-16:00")
    print(f"  对应香港时间: 21:30-04:00 (次日)")

    print("\n" + "=" * 60)

    # 返回结果用于自动化测试
    return {
        "current_market": current_market,
        "active_symbols": active_symbols,
        "hk_time": now_hk,
        "us_time": now_us
    }


if __name__ == "__main__":
    result = test_market_hours()

    # 根据市场给出建议
    if result["current_market"] == "HK":
        print("✅ 当前处于港股交易时段，Regime通知将使用港股指数（HSI.HK）")
    elif result["current_market"] == "US":
        print("✅ 当前处于美股交易时段，Regime通知将使用美股指数（SPY.US）")
    else:
        print("⏰ 当前非交易时段，Regime通知将被跳过")
