#!/usr/bin/env python3
"""测试港股交易时间判断修复"""

from datetime import datetime, time

def is_market_open_time_old(current_time: time) -> bool:
    """修复前的逻辑（错误）"""
    morning = time(9, 30) <= current_time <= time(12, 0)
    afternoon = time(13, 0) <= current_time <= time(15, 0)  # ❌ 错误：应该是16:00
    return morning or afternoon

def is_market_open_time_new(current_time: time) -> bool:
    """修复后的逻辑（正确）"""
    morning = time(9, 30) <= current_time <= time(12, 0)
    afternoon = time(13, 0) <= current_time <= time(16, 0)  # ✅ 正确：到16:00
    return morning or afternoon

print("=" * 70)
print("🧪 测试港股交易时间判断修复")
print("=" * 70)

# 测试用例
test_cases = [
    (time(9, 0), False, "开盘前"),
    (time(9, 30), True, "上午开盘"),
    (time(10, 0), True, "上午交易中"),
    (time(11, 59), True, "上午快收盘"),
    (time(12, 0), True, "上午收盘时刻"),
    (time(12, 30), False, "中午休市"),
    (time(13, 0), True, "下午开盘"),
    (time(14, 0), True, "下午交易中"),
    (time(15, 0), True, "下午15:00"),
    (time(15, 13), True, "下午15:13（用户反馈时间）"),
    (time(15, 30), True, "下午15:30"),
    (time(15, 59), True, "快收盘"),
    (time(16, 0), True, "收盘竞价"),
    (time(16, 1), False, "收盘后"),
]

print(f"\n📋 测试结果对比:")
print(f"{'时间':<12} {'预期结果':<12} {'修复前':<12} {'修复后':<12} {'修复前状态':<15} {'修复后状态':<15} {'说明':<20}")
print("-" * 110)

errors_old = 0
errors_new = 0

for test_time, expected, description in test_cases:
    result_old = is_market_open_time_old(test_time)
    result_new = is_market_open_time_new(test_time)

    status_old = "✅ 正确" if result_old == expected else "❌ 错误"
    status_new = "✅ 正确" if result_new == expected else "❌ 错误"

    if result_old != expected:
        errors_old += 1
    if result_new != expected:
        errors_new += 1

    time_str = test_time.strftime("%H:%M")
    expected_str = "开盘" if expected else "休市"
    old_str = "开盘" if result_old else "休市"
    new_str = "开盘" if result_new else "休市"

    print(f"{time_str:<12} {expected_str:<12} {old_str:<12} {new_str:<12} {status_old:<15} {status_new:<15} {description:<20}")

print("\n" + "=" * 70)
print("📊 统计结果")
print("=" * 70)

print(f"\n总测试用例: {len(test_cases)}")
print(f"\n修复前:")
print(f"  ❌ 错误: {errors_old}/{len(test_cases)}")
print(f"  ✅ 正确: {len(test_cases) - errors_old}/{len(test_cases)}")
print(f"  准确率: {(len(test_cases) - errors_old)/len(test_cases)*100:.1f}%")

print(f"\n修复后:")
print(f"  ❌ 错误: {errors_new}/{len(test_cases)}")
print(f"  ✅ 正确: {len(test_cases) - errors_new}/{len(test_cases)}")
print(f"  准确率: {(len(test_cases) - errors_new)/len(test_cases)*100:.1f}%")

print("\n" + "=" * 70)
print("🎯 关键修复")
print("=" * 70)

print("\n**问题根源**:")
print("  修复前: 下午交易时段判断为 13:00-15:00 ❌")
print("  修复后: 下午交易时段判断为 13:00-16:00 ✅")

print("\n**影响范围**:")
print("  15:00-16:00 之间的时段被错误判断为休市")
print("  导致实时挪仓和紧急卖出检查被跳过")

print("\n**用户反馈案例**:")
print("  时间: 北京时间15:13")
print("  修复前: 判断为休市 ❌")
print("  修复后: 判断为开盘 ✅")
print("  影响: 系统恢复正常监控")

print("\n**港股交易时间（正确）**:")
print("  上午时段: 09:30-12:00")
print("  中午休市: 12:00-13:00")
print("  下午时段: 13:00-16:00 (包含16:00收盘竞价)")

print("\n" + "=" * 70)

if errors_new == 0:
    print("✅ 修复成功！所有测试用例通过")
else:
    print(f"❌ 修复后仍有 {errors_new} 个测试失败")

print("=" * 70)
