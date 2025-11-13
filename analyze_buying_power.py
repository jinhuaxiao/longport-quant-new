#!/usr/bin/env python3
"""分析 700.HK 购买力不足的原因"""

# 700.HK (腾讯)
symbol = "700.HK"
current_price = 650.0
lot_size = 100  # 港股一手100股

# 账户资金情况（从日志获取）
hkd_available = -2172529.57  # 可用资金（负数表示使用融资）
hkd_buying_power = 14242.86  # 购买力
usd_available = -617.20
usd_buying_power = 1827.76

print("="*70)
print(f"📊 {symbol} (腾讯) 购买力分析")
print("="*70)

# 计算一手成本
one_lot_cost = current_price * lot_size
print(f"\n💰 价格信息:")
print(f"   当前价格: ${current_price:,.2f}")
print(f"   一手股数: {lot_size} 股")
print(f"   一手成本: ${one_lot_cost:,.2f}")

# 账户资金状态
print(f"\n💳 HKD 账户状态:")
print(f"   可用资金: ${hkd_available:,.2f} ❌ (负数 = 使用融资)")
print(f"   购买力: ${hkd_buying_power:,.2f}")
print(f"   可买手数: {hkd_buying_power / one_lot_cost:.4f} 手")
print(f"   可买股数: {int(hkd_buying_power / current_price)} 股")

# 判断
print(f"\n📋 结论:")
if hkd_buying_power < one_lot_cost:
    print(f"   ❌ 购买力不足！")
    print(f"   需要: ${one_lot_cost:,.2f}")
    print(f"   实际: ${hkd_buying_power:,.2f}")
    print(f"   差额: ${one_lot_cost - hkd_buying_power:,.2f}")
    print()
    print(f"   🔍 原因分析:")
    print(f"      1. 账户使用了融资（可用资金为负）")
    print(f"      2. 一手 {symbol} 需要 ${one_lot_cost:,.2f}")
    print(f"      3. 当前购买力只有 ${hkd_buying_power:,.2f}")
    print(f"      4. 无法凑齐一手，API 返回可买数量 = 0")
else:
    print(f"   ✅ 购买力充足！")
    print(f"   可买 {int(hkd_buying_power / one_lot_cost)} 手")

print(f"\n💡 解决方案:")
print(f"   1. 等待现有持仓卖出，释放资金")
print(f"   2. 实时挪仓：系统会自动分析并卖出弱势持仓")
print(f"   3. 考虑买入更便宜的标的（价格 < ${hkd_buying_power / lot_size:.2f}/股）")
print(f"   4. 增加账户资金")

print("\n" + "="*70)
print("🔄 实时挪仓功能状态:")
print("="*70)
print("✅ 已启用（REALTIME_ROTATION_ENABLED=true）")
print("📊 触发条件：")
print("   - 检测到高分信号（≥60分）因资金不足延迟")
print("   - 自动寻找弱势持仓（评分低于新信号10+分）")
print("   - 卖出最弱持仓释放资金")
print("   - 优先级95分，快速执行")
print("\n💬 系统应该已发送持仓分析通知到 Slack/Discord")
print("="*70)
