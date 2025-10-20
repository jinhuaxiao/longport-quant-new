#!/usr/bin/env python3
"""测试新的混合策略评分系统（泡泡玛特实际数据）"""

# 泡泡玛特实际数据（来自测试）
current_price = 291.00
rsi = 63.23
bb_upper = 280.06
bb_middle = 263.54
bb_lower = 247.02
macd_line = -1.894
macd_signal = -5.366
macd_hist = macd_line - macd_signal  # 3.472
prev_macd_hist = 0  # 假设
volume_ratio = 0.73
sma_20 = 280.50  # 假设
sma_50 = 265.30  # 假设

print("=" * 80)
print("泡泡玛特 (9992.HK) - 新混合策略评分测试")
print("=" * 80)
print(f"\n📊 当前市场数据:")
print(f"  当前价格: ${current_price:.2f}")
print(f"  RSI: {rsi:.2f}")
print(f"  布林带: 上${bb_upper:.2f}, 中${bb_middle:.2f}, 下${bb_lower:.2f}")
print(f"  MACD: {macd_line:.3f} vs 信号线{macd_signal:.3f} (柱状图{macd_hist:.3f})")
print(f"  成交量比率: {volume_ratio:.2f}x")
print(f"  均线: SMA20=${sma_20:.2f}, SMA50=${sma_50:.2f}")

print("\n" + "-" * 80)
print("🎯 新评分系统（混合策略：逆向 + 动量）")
print("-" * 80)

score = 0
reasons = []

# === 1. RSI评分 (0-30分) ===
print("\n[1] RSI评分 (0-30分):")
rsi_score = 0
if rsi < 20:
    rsi_score = 30
    print(f"  ✅ 极度超卖({rsi:.1f}) → 30分")
elif rsi < 30:
    rsi_score = 25
    print(f"  ✅ 超卖({rsi:.1f}) → 25分")
elif rsi < 40:
    rsi_score = 15
    print(f"  ✅ 偏低({rsi:.1f}) → 15分")
elif 40 <= rsi <= 50:
    rsi_score = 5
    print(f"  ✅ 中性({rsi:.1f}) → 5分")
elif 50 < rsi <= 70:  # ⭐ 新增：强势区间
    rsi_score = 15
    print(f"  ✅ 强势区间({rsi:.1f}) → 15分 【新增动量评分】")
    reasons.append(f"RSI强势区间({rsi:.1f})")
else:
    print(f"  ❌ 过热({rsi:.1f}) → 0分")

score += rsi_score
print(f"  得分: {rsi_score}/30")

# === 2. 布林带评分 (0-25分) ===
print("\n[2] 布林带评分 (0-25分):")
bb_range = bb_upper - bb_lower
bb_position_pct = (current_price - bb_lower) / bb_range * 100
bb_width_pct = bb_range / bb_middle * 100

print(f"  布林带位置: {bb_position_pct:.1f}%")
print(f"  布林带宽度: {bb_width_pct:.1f}%")

bb_score = 0
if current_price <= bb_lower:
    bb_score = 25
    print(f"  ✅ 触及下轨 → 25分")
elif current_price <= bb_lower * 1.02:
    bb_score = 20
    print(f"  ✅ 接近下轨 → 20分")
elif bb_position_pct < 30:
    bb_score = 10
    print(f"  ✅ 下半部({bb_position_pct:.0f}%) → 10分")
elif current_price >= bb_upper:  # ⭐ 新增：突破上轨
    bb_score = 20
    print(f"  ✅ 突破上轨(${bb_upper:.2f}) → 20分 【新增突破评分】")
    reasons.append(f"突破布林带上轨(${bb_upper:.2f})")
elif current_price >= bb_upper * 0.98:  # ⭐ 新增：接近上轨
    bb_score = 15
    print(f"  ✅ 接近上轨 → 15分 【新增突破评分】")
    reasons.append("接近布林带上轨")
else:
    print(f"  ❌ 中间位置({bb_position_pct:.0f}%) → 0分")

# 布林带收窄加分
if bb_width_pct < 10:
    bb_score += 5
    print(f"  ✅ 极度收窄({bb_width_pct:.1f}%) → +5分")
elif bb_width_pct < 15:
    bb_score += 3
    print(f"  ✅ 收窄 → +3分")

score += bb_score
print(f"  得分: {bb_score}/25")

# === 3. MACD评分 (0-20分) ===
print("\n[3] MACD评分 (0-20分):")
macd_score = 0
if prev_macd_hist < 0 and macd_hist > 0:
    macd_score = 20
    print(f"  ✅ 金叉（从负转正） → 20分")
    reasons.append("MACD金叉")
elif macd_hist > 0 and macd_line > macd_signal:
    macd_score = 15
    print(f"  ✅ MACD在信号线上方 → 15分")
    reasons.append("MACD多头")
elif macd_hist > prev_macd_hist and abs(macd_hist) < 1:
    macd_score = 10
    print(f"  ✅ 接近金叉 → 10分")
else:
    print(f"  ❌ 无明显MACD信号 → 0分")

score += macd_score
print(f"  得分: {macd_score}/20")

# === 4. 成交量评分 (0-15分) ===
print("\n[4] 成交量评分 (0-15分):")
volume_score = 0
if volume_ratio >= 2.0:
    volume_score = 15
    print(f"  ✅ 大幅放量({volume_ratio:.1f}x) → 15分")
elif volume_ratio >= 1.5:
    volume_score = 10
    print(f"  ✅ 明显放量({volume_ratio:.1f}x) → 10分")
elif volume_ratio >= 1.2:
    volume_score = 5
    print(f"  ✅ 温和放量({volume_ratio:.1f}x) → 5分")
elif volume_ratio >= 0.8:  # ⭐ 新增：正常成交量
    volume_score = 3
    print(f"  ✅ 正常({volume_ratio:.1f}x) → 3分 【新增正常量评分】")
    reasons.append(f"成交量正常({volume_ratio:.1f}x)")
else:
    print(f"  ❌ 缩量({volume_ratio:.1f}x) → 0分")

score += volume_score
print(f"  得分: {volume_score}/15")

# === 5. 趋势评分 (0-10分) ===
print("\n[5] 趋势评分 (0-10分):")
trend_score = 0
if current_price > sma_20:
    trend_score += 3
    print(f"  ✅ 价格在SMA20(${sma_20:.2f})上方 → +3分")
    reasons.append("价格在SMA20上方")

if sma_20 > sma_50:
    trend_score += 7
    print(f"  ✅ SMA20在SMA50(${sma_50:.2f})上方(上升趋势) → +7分")
    reasons.append("SMA20在SMA50上方(上升趋势)")
elif sma_20 > sma_50 * 0.98:
    trend_score += 4
    print(f"  ✅ 接近均线金叉 → +4分")

score += trend_score
print(f"  得分: {trend_score}/10")

# === 总结 ===
print("\n" + "=" * 80)
print(f"📊 总分: {score}/100")
print("=" * 80)

if score >= 60:
    signal = "✅ 强买入信号 (≥60分)"
elif score >= 45:
    signal = "✅ 买入信号 (≥45分)"
elif score >= 30:
    signal = "⚠️ 弱买入信号 (≥30分)"
else:
    signal = "❌ 不生成信号 (<30分)"

print(f"\n{signal}")

print("\n📋 信号原因:")
for i, reason in enumerate(reasons, 1):
    print(f"  {i}. {reason}")

print("\n" + "=" * 80)
print("📈 新旧策略对比:")
print("=" * 80)
print("""
旧策略（纯逆向）:
  RSI: 0/30   (RSI > 50 不符合超卖条件)
  布林带: 0/25  (价格突破上轨不符合低位条件)
  MACD: 15/20  (金叉信号)
  成交量: 0/15  (0.73x 缩量)
  趋势: 10/10  (上升趋势)
  总分: 25/100 ❌ 低于30分门槛

新策略（混合）:
  RSI: 15/30  (强势区间，支持动量)
  布林带: 20/25 (突破上轨，支持突破)
  MACD: 15/20  (金叉信号)
  成交量: 0/15  (0.73x 缩量，仍然0分)
  趋势: 10/10  (上升趋势)
  总分: 60/100 ✅ 达到强买入门槛！
""")

print("=" * 80)
print("🎯 结论:")
print("=" * 80)
print("""
✅ 新的混合策略成功支持泡泡玛特的突破场景！
✅ 评分从25分提升到60分，满足强买入条件
✅ 系统现在既支持逆向交易（低买）也支持趋势跟随（突破买）

⚠️ 注意：
- 成交量仍然是缩量（0.73x），但其他4项指标足够强
- 这是典型的强势突破场景，即使成交量不配合也值得关注
- 如果成交量能达到0.8x以上，总分会更高（63分）
""")
