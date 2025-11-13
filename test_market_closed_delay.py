#!/usr/bin/env python3
"""测试市场休市时延迟订单逻辑"""

from datetime import datetime
from zoneinfo import ZoneInfo
import sys
sys.path.insert(0, '/data/web/longport-quant-new')

from src.longport_quant.utils.market_hours import MarketHours

print("=" * 70)
print("🧪 测试市场休市时延迟订单逻辑")
print("=" * 70)

# 测试用例
test_symbols = ["CRWV.US", "AAPL.US", "700.HK", "3690.HK"]

print(f"\n⏰ 当前时间:")
beijing_now = datetime.now(ZoneInfo('Asia/Shanghai'))
us_now = datetime.now(ZoneInfo('America/New_York'))
hk_now = datetime.now(ZoneInfo('Asia/Hong_Kong'))

print(f"   北京时间: {beijing_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
print(f"   美东时间: {us_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
print(f"   香港时间: {hk_now.strftime('%Y-%m-%d %H:%M:%S %Z')}")

print(f"\n📊 当前市场状态: {MarketHours.get_current_market()}")

print(f"\n📋 各标的市场状态和延迟时间:")
print(f"{'标的':<15} {'市场':<8} {'是否开盘':<12} {'距离开盘':<15} {'建议延迟':<15} {'说明':<30}")
print("-" * 105)

for symbol in test_symbols:
    market = MarketHours.get_market_for_symbol(symbol)
    is_open = MarketHours.is_market_open_for_symbol(symbol)
    minutes_until_open = MarketHours.get_minutes_until_next_open(symbol)

    # 计算延迟时间（与订单执行器逻辑一致）
    if minutes_until_open == 0:
        delay_minutes = 0
        action = "立即执行"
    elif minutes_until_open > 480:  # >8小时
        delay_minutes = minutes_until_open - 30
        hours = delay_minutes // 60
        mins = delay_minutes % 60
        action = f"延迟到开盘前30分钟（约{hours}小时{mins}分钟）"
    else:
        delay_minutes = minutes_until_open
        hours = delay_minutes // 60
        mins = delay_minutes % 60
        action = f"延迟到开盘（约{hours}小时{mins}分钟）"

    status = "✅ 开盘" if is_open else "❌ 休市"
    until_open = f"{minutes_until_open}分钟" if minutes_until_open > 0 else "已开盘"
    delay_str = f"{delay_minutes}分钟" if delay_minutes > 0 else "-"

    print(f"{symbol:<15} {market:<8} {status:<12} {until_open:<15} {delay_str:<15} {action:<30}")

print("\n" + "=" * 70)
print("📊 逻辑说明")
print("=" * 70)

print("\n🔧 **延迟策略**:")
print("   1. 市场开盘 → 立即执行订单")
print("   2. 距离开盘 ≤ 8小时 → 延迟到开盘时间")
print("   3. 距离开盘 > 8小时 → 延迟到开盘前30分钟")
print("      （避免过长延迟，保持信号新鲜度）")

print("\n⏰ **时间计算**:")
print("   - 港股：09:30-12:00, 13:00-16:00")
print("   - 美股：09:30-16:00 (ET)")
print("   - 周末：自动推迟到下周一09:30")
print("   - 中午休市：延迟到13:00（仅港股）")

print("\n💡 **修复前后对比**:")
print("   修复前：")
print("     - 休市时提交限价单")
print("     - 订单一直挂单等待")
print("     - 可能无法成交或延迟成交")
print("")
print("   修复后：")
print("     - 休市时延迟信号")
print("     - 开盘时自动执行")
print("     - 提高成交概率")

print("\n" + "=" * 70)
print("✅ 测试完成")
print("=" * 70)

# 模拟CRWV.US的情况
print("\n🎯 **CRWV.US 案例**:")
print(f"   当前时间: 北京时间 14:27 (美东时间 02:27)")
print(f"   市场状态: 休市")
minutes_until_open = MarketHours.get_minutes_until_next_open("CRWV.US")
print(f"   距离开盘: {minutes_until_open}分钟 ({minutes_until_open//60}小时{minutes_until_open%60}分钟)")
delay = max(1, minutes_until_open)
print(f"   延迟时间: {delay}分钟")
print(f"   执行时间: 美东时间 09:30 开盘")
print("")
print(f"   ✅ 订单将延迟 {delay//60}小时{delay%60}分钟 后在开盘时自动执行")
print("=" * 70)
