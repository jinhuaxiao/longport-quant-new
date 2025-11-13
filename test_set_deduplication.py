#!/usr/bin/env python3
"""测试去重逻辑修复"""

print("="*70)
print("🔧 测试字典列表去重修复")
print("="*70)

# 模拟数据
sell_positions = [
    {'symbol': '941.HK', 'score': 50},
    {'symbol': '386.HK', 'score': 45},
]

opportunity_cost_positions = [
    {'symbol': '386.HK', 'score': 45},  # 重复
    {'symbol': '5.HK', 'score': 40},
]

print("\n原始数据:")
print(f"sell_positions: {sell_positions}")
print(f"opportunity_cost_positions: {opportunity_cost_positions}")

# ❌ 旧方法（会报错）
print("\n❌ 旧方法: list(set(a + b))")
try:
    result_old = list(set(sell_positions + opportunity_cost_positions))
    print(f"结果: {result_old}")
except TypeError as e:
    print(f"错误: {e}")

# ✅ 新方法（按 symbol 去重）
print("\n✅ 新方法: 按 symbol 去重")
seen_symbols = set()
potential_sell_positions = []
for pos in (sell_positions + opportunity_cost_positions):
    symbol = pos['symbol']
    if symbol not in seen_symbols:
        seen_symbols.add(symbol)
        potential_sell_positions.append(pos)

print(f"结果: {potential_sell_positions}")
print(f"数量: {len(potential_sell_positions)} (预期3个)")

# 验证
assert len(potential_sell_positions) == 3, "去重失败"
assert potential_sell_positions[0]['symbol'] == '941.HK', "顺序错误"
assert potential_sell_positions[1]['symbol'] == '386.HK', "顺序错误"
assert potential_sell_positions[2]['symbol'] == '5.HK', "顺序错误"

print("\n✅ 测试通过！")
print("="*70)
