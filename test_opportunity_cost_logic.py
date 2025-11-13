#!/usr/bin/env python3
"""测试机会成本分析逻辑"""

# 模拟场景：700.HK 新信号评分55分，账户使用融资

print("="*70)
print("🔍 机会成本分析逻辑测试")
print("="*70)

# 新信号
new_signal = {
    'symbol': '700.HK',
    'score': 55,
    'price': 650.0
}

# 模拟持仓（卖出评分）
positions = [
    {'symbol': '941.HK', 'sell_score': 10, 'profit': '+5%', 'value': 1097075},
    {'symbol': '386.HK', 'sell_score': 15, 'profit': '+2%', 'value': 458980},
    {'symbol': '2378.HK', 'sell_score': 5, 'profit': '+8%', 'value': 1387525},
    {'symbol': '1088.HK', 'sell_score': 20, 'profit': '+12%', 'value': 64960},
    {'symbol': '5.HK', 'sell_score': 8, 'profit': '-3%', 'value': 43560},
]

# 账户状态
using_margin = True
hkd_available = -2172529.57
hkd_buying_power = 14242.86

print(f"\n📊 新信号：{new_signal['symbol']} 评分 {new_signal['score']}/100")
print(f"💼 账户：使用融资（可用资金: ${hkd_available:,.2f}）")
print(f"\n📦 持仓分析：")
print(f"{'标的':<12} {'卖出评分':<10} {'持有评分':<10} {'评分差':<10} {'是否轮换':<10}")
print("-"*70)

# 原逻辑：只看技术面弱势（卖出评分≥40）
weak_positions_old = []

# 新逻辑：机会成本分析
opportunity_positions_new = []

for pos in positions:
    sell_score = pos['sell_score']
    holding_score = 100 - sell_score  # 持有评分
    score_diff = new_signal['score'] - holding_score  # 新信号 vs 持有评分

    # 原逻辑判断
    is_weak_old = sell_score >= 40

    # 新逻辑判断：融资账户 + 新信号≥50 + 评分差>20
    is_opportunity_new = using_margin and new_signal['score'] >= 50 and score_diff > 20

    if is_weak_old:
        weak_positions_old.append(pos)
    if is_opportunity_new:
        opportunity_positions_new.append(pos)

    # 显示结果
    rotation = ""
    if is_weak_old:
        rotation = "🔴 技术弱势"
    elif is_opportunity_new:
        rotation = "🟡 机会成本"
    else:
        rotation = "🟢 继续持有"

    print(f"{pos['symbol']:<12} {sell_score:<10} {holding_score:<10} {score_diff:+<10.0f} {rotation:<10}")

print("\n" + "="*70)
print("📈 结果对比：")
print("="*70)

print(f"\n🔵 原逻辑（只看技术面弱势）：")
if weak_positions_old:
    print(f"   找到 {len(weak_positions_old)} 个可卖出持仓")
    for pos in weak_positions_old:
        print(f"   - {pos['symbol']} (卖出评分{pos['sell_score']})")
else:
    print(f"   ❌ 无可卖出持仓（没有卖出评分≥40的持仓）")
    print(f"   结果：700.HK 信号被丢弃，不下单")

print(f"\n🟢 新逻辑（机会成本分析）：")
if opportunity_positions_new:
    print(f"   ✅ 找到 {len(opportunity_positions_new)} 个可轮换持仓")
    for pos in opportunity_positions_new:
        holding_score = 100 - pos['sell_score']
        print(f"   - {pos['symbol']} (持有评分{holding_score:.0f} < 新信号{new_signal['score']})")
    print(f"   结果：发送完整分析通知，建议手动卖出并买入 700.HK")
else:
    print(f"   ❌ 无可轮换持仓")
    print(f"   结果：发送简化通知，等待资金补充")

print("\n" + "="*70)
print("💡 改进说明：")
print("="*70)
print("✅ 新逻辑不再仅依赖技术面弱势判断")
print("✅ 考虑机会成本：新信号(55) vs 持仓(80-95)")
print("✅ 当新信号评分高于持仓评分20+分时，建议轮换")
print("✅ 适用于融资账户（必须卖出才能买入新标的）")
print("="*70)
