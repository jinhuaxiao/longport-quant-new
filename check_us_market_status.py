#!/usr/bin/env python3
"""检查美股当前交易状态"""

from datetime import datetime
import pytz

# 北京时间
beijing_tz = pytz.timezone('Asia/Shanghai')
now_beijing = datetime.now(beijing_tz)

# 转换到美东时间
ny_tz = pytz.timezone('America/New_York')
now_ny = now_beijing.astimezone(ny_tz)

print("\n" + "="*70)
print("🕐 当前时间")
print("="*70)
print(f"北京时间: {now_beijing.strftime('%Y-%m-%d %H:%M:%S %Z')}")
print(f"美东时间: {now_ny.strftime('%Y-%m-%d %H:%M:%S %Z')}")

# 判断美股交易时段
hour = now_ny.hour
minute = now_ny.minute
weekday = now_ny.weekday()

print("\n" + "="*70)
print("📊 美股交易时段判断")
print("="*70)

if weekday >= 5:  # 周六、周日
    print("❌ 状态: 周末休市")
elif (hour == 4 and minute >= 0) or (hour > 4 and hour < 9) or (hour == 9 and minute < 30):
    print("🌅 状态: 盘前时段 (04:00 - 09:30 ET)")
    print("✅ 盘前交易: 已启用（ENABLE_US_PREMARKET_SIGNALS=true）")
    print("💡 应该生成买入信号（评分权重80%）")
elif (hour == 9 and minute >= 30) or (hour > 9 and hour < 16):
    print("✅ 状态: 正常交易时段 (09:30 - 16:00 ET)")
    print("💡 应该正常生成买入信号")
elif hour >= 16 and hour < 20:
    print("🌆 状态: 盘后时段 (16:00 - 20:00 ET)")
    print("⚠️  盘后交易: 仅紧急减仓（ENABLE_AFTERHOURS_REBALANCE）")
else:
    print("❌ 状态: 收盘休市")

print("\n" + "="*70)
print("💡 北京时间对应关系：")
print("   - 盘前：16:00 - 21:30（北京时间）")
print("   - 正式交易：21:30 - 04:00（北京时间次日）")
print("   - 盘后：04:00 - 08:00（北京时间次日）")
print("="*70)
