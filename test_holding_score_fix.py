#!/usr/bin/env python3
"""测试持有评分修复效果"""

print("="*70)
print("🔧 持有评分修复效果测试")
print("="*70)

def convert_sell_to_holding_score(sell_score: int) -> int:
    """
    将卖出评分转换为持有评分（改进版）
    """
    if sell_score >= 60:
        return max(0, 20 - (sell_score - 60) // 2)
    elif sell_score >= 40:
        return 40 - (sell_score - 40)
    elif sell_score >= 20:
        return 60 - (sell_score - 20)
    else:
        return 80 - sell_score

# 测试用例
test_cases = [
    # (卖出评分, 旧算法持有评分, 新算法持有评分, 解释)
    (0, 100, 80, "无任何卖出信号"),
    (5, 95, 75, "几乎无卖出信号"),
    (10, 90, 70, "极少卖出信号"),
    (15, 85, 65, "少量卖出信号"),
    (20, 80, 60, "有一些卖出信号（阈值）"),
    (30, 70, 50, "较多卖出信号"),
    (40, 60, 40, "达到卖出阈值"),
    (50, 50, 30, "超过卖出阈值"),
    (60, 40, 20, "强烈卖出信号"),
    (80, 20, 10, "极强卖出信号"),
    (100, 0, 0, "所有卖出条件满足"),
]

print(f"\n{'卖出评分':<10} {'旧算法':<10} {'新算法':<10} {'变化':<10} {'解释':<20}")
print("-"*70)

for sell_score, old_holding, expected_new, explanation in test_cases:
    new_holding = convert_sell_to_holding_score(sell_score)
    diff = new_holding - old_holding
    diff_str = f"{diff:+d}" if diff != 0 else "0"

    # 验证
    assert new_holding == expected_new, f"卖出{sell_score}分: 期望{expected_new}, 实际{new_holding}"

    print(f"{sell_score:<10} {old_holding:<10} {new_holding:<10} {diff_str:<10} {explanation:<20}")

print("\n" + "="*70)
print("📊 700.HK 案例分析")
print("="*70)

# 700.HK 实际案例
new_signal_score = 55  # 700.HK买入评分
positions = [
    ("941.HK", 10),
    ("386.HK", 15),
    ("2378.HK", 5),
    ("1088.HK", 20),
    ("5.HK", 8),
]

print(f"\n🆕 新信号: 700.HK 买入评分 = {new_signal_score}分")
print(f"\n📦 现有持仓对比：")
print(f"{'标的':<12} {'卖出评分':<10} {'旧持有评分':<12} {'新持有评分':<12} {'轮换判断（旧）':<15} {'轮换判断（新）':<15}")
print("-"*100)

rotation_threshold = 20  # 需要高出20分

for symbol, sell_score in positions:
    old_holding = 100 - sell_score
    new_holding = convert_sell_to_holding_score(sell_score)

    # 旧逻辑判断
    old_should_rotate = new_signal_score > (old_holding + rotation_threshold)
    old_result = "✅ 轮换" if old_should_rotate else "❌ 不轮换"

    # 新逻辑判断
    new_should_rotate = new_signal_score > (new_holding + rotation_threshold)
    new_result = "✅ 轮换" if new_should_rotate else "❌ 不轮换"

    print(f"{symbol:<12} {sell_score:<10} {old_holding:<12} {new_holding:<12} {old_result:<15} {new_result:<15}")

print("\n" + "="*70)
print("🎯 修复效果总结")
print("="*70)

print("\n✅ **核心改进**：")
print("   1. 避免了简单的 100-x 反向转换")
print("   2. 持有评分不再虚高（从90-95降到60-75）")
print("   3. 买入评分(55)和持有评分(60-75)处于相近量级，可比性增强")

print("\n📈 **评分映射逻辑**：")
print("   卖出0-20分  → 持有60-80分（中性持仓，不是优质）")
print("   卖出20-40分 → 持有40-60分（中性偏弱）")
print("   卖出40-60分 → 持有20-40分（弱势持仓）")
print("   卖出60+分   → 持有0-20分（极弱持仓）")

print("\n💡 **实际影响（700.HK案例）**：")
print("   修复前: 新信号55分 vs 持仓90-95分 → 看起来远不如现有持仓")
print("   修复后: 新信号55分 vs 持仓60-75分 → 更接近，评分可比")
print("   结论: 虽然仍不轮换（55 < 60+20），但评分差距更合理")

print("\n⚠️  **重要说明**：")
print("   持有评分降低后，轮换会更容易触发")
print("   建议关注首次运行后的轮换决策是否合理")
print("   必要时可调整轮换阈值（当前20分）")

print("\n" + "="*70)
