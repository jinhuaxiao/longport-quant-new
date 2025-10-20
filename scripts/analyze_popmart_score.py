#!/usr/bin/env python3
"""分析泡泡玛特的评分计算"""

# 根据测试数据：
current_price = 291.00
rsi = 63.23
bb_upper = 280.06
bb_middle = 263.54
bb_lower = 247.02
macd_line = -1.894
macd_signal = -5.366
macd_hist = macd_line - macd_signal  # 3.472
prev_macd_hist = 0  # 假设
volume_ratio = 0.73  # 缩量

print("=" * 70)
print("泡泡玛特 (9992.HK) 评分分析")
print("=" * 70)
print(f"\n当前价格: ${current_price:.2f}")
print(f"RSI: {rsi:.2f}")
print(f"布林带: 上${bb_upper:.2f}, 中${bb_middle:.2f}, 下${bb_lower:.2f}")
print(f"MACD: {macd_line:.3f} vs 信号线{macd_signal:.3f}")
print(f"成交量比率: {volume_ratio:.2f}x")

print("\n" + "-" * 70)
print("评分计算:")
print("-" * 70)

score = 0
reasons = []

# 1. RSI分析 (0-30分)
print("\n[1] RSI评分 (0-30分):")
rsi_score = 0
if rsi < 20:
    rsi_score = 30
    print(f"  极度超卖({rsi:.1f}) → 30分")
elif rsi < 30:
    rsi_score = 25
    print(f"  超卖({rsi:.1f}) → 25分")
elif rsi < 40:
    rsi_score = 15
    print(f"  偏低({rsi:.1f}) → 15分")
elif 40 <= rsi <= 50:
    rsi_score = 5
    print(f"  中性({rsi:.1f}) → 5分")
else:
    print(f"  ❌ 偏高({rsi:.1f}) → 0分")
    print(f"     说明: RSI > 50，不是超卖区域，不符合买入条件")

score += rsi_score
print(f"  得分: {rsi_score}/30")

# 2. 布林带分析 (0-25分)
print("\n[2] 布林带评分 (0-25分):")
bb_range = bb_upper - bb_lower
bb_position_pct = (current_price - bb_lower) / bb_range * 100
bb_width_pct = bb_range / bb_middle * 100

print(f"  布林带位置: {bb_position_pct:.1f}%")
print(f"  布林带宽度: {bb_width_pct:.1f}%")

bb_score = 0
if current_price <= bb_lower:
    bb_score = 25
    print(f"  触及下轨 → 25分")
elif current_price <= bb_lower * 1.02:
    bb_score = 20
    print(f"  接近下轨 → 20分")
elif bb_position_pct < 30:
    bb_score = 10
    print(f"  下半部({bb_position_pct:.0f}%) → 10分")
else:
    print(f"  ❌ 位置{bb_position_pct:.0f}% → 0分")
    print(f"     说明: 价格在{bb_position_pct:.0f}%位置，甚至突破上轨，不是低位")

# 布林带收窄加分
if bb_width_pct < 10:
    bb_score += 5
    print(f"  极度收窄({bb_width_pct:.1f}%) → +5分")
elif bb_width_pct < 15:
    bb_score += 3
    print(f"  收窄 → +3分")

score += bb_score
print(f"  得分: {bb_score}/25")

# 3. MACD分析 (0-20分)
print("\n[3] MACD评分 (0-20分):")
macd_score = 0
if prev_macd_hist < 0 and macd_hist > 0:
    macd_score = 20
    print(f"  ✅ 金叉（从负转正） → 20分")
elif macd_hist > 0 and macd_line > macd_signal:
    macd_score = 15
    print(f"  ✅ MACD在信号线上方 → 15分")
elif macd_hist > prev_macd_hist and abs(macd_hist) < 1:
    macd_score = 10
    print(f"  接近金叉 → 10分")
else:
    print(f"  ❌ 无明显MACD信号 → 0分")

score += macd_score
print(f"  得分: {macd_score}/20")

# 4. 成交量分析 (0-15分)
print("\n[4] 成交量评分 (0-15分):")
volume_score = 0
if volume_ratio >= 2.0:
    volume_score = 15
    print(f"  大幅放量({volume_ratio:.1f}x) → 15分")
elif volume_ratio >= 1.5:
    volume_score = 10
    print(f"  明显放量({volume_ratio:.1f}x) → 10分")
elif volume_ratio >= 1.2:
    volume_score = 5
    print(f"  温和放量({volume_ratio:.1f}x) → 5分")
else:
    print(f"  ❌ 正常或缩量({volume_ratio:.1f}x) → 0分")

score += volume_score
print(f"  得分: {volume_score}/15")

# 5. 趋势分析 (0-10分)
print("\n[5] 趋势评分 (0-10分):")
print(f"  (需要SMA20和SMA50数据，这里假设上升趋势)")
trend_score = 10  # 假设
score += trend_score
print(f"  得分: {trend_score}/10")

# 总结
print("\n" + "=" * 70)
print(f"总分: {score}/100")
print("=" * 70)

if score >= 60:
    print("✅ 结果: 强买入信号 (≥60分)")
elif score >= 45:
    print("✅ 结果: 买入信号 (≥45分)")
elif score >= 30:
    print("⚠️ 结果: 弱买入信号 (≥30分)")
else:
    print("❌ 结果: 不生成信号 (<30分)")

print("\n" + "=" * 70)
print("问题分析:")
print("=" * 70)
print("""
1. RSI 63.23 太高 (>50) → 0分
   系统认为这不是超卖区域，不是好的买入点

2. 价格突破布林带上轨 → 0分
   系统认为价格过高，不是低位买入机会

3. 成交量缩量 (0.73x) → 0分
   没有成交量配合

4. MACD金叉 → 可能15分
   这是唯一的正面信号

总结：
- 当前系统是"逆向交易策略"（低买高卖）
- 倾向于在RSI超卖、价格接近下轨时买入
- 泡泡玛特当前处于相对高位，突破上轨
- 系统认为这是"追高"，风险较大，不建议买入

如果想在突破时买入（趋势跟随策略），需要调整评分规则。
""")
