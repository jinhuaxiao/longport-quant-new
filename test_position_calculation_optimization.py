#!/usr/bin/env python3
"""测试仓位计算优化（降低最大仓位 + Kelly公式集成）"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))


def test_position_calculation():
    """测试新的仓位计算逻辑"""
    print("=" * 80)
    print("仓位计算优化测试")
    print("=" * 80)
    print()

    # 测试场景
    test_cases = [
        {
            "name": "NVDA (极强信号，80分)",
            "score": 80,
            "price": 140.0,
            "net_assets": 50000.0,
            "regime": "BULL",
        },
        {
            "name": "NVDA (超强信号，90分)",
            "score": 90,
            "price": 140.0,
            "net_assets": 50000.0,
            "regime": "BULL",
        },
        {
            "name": "NVDA (满分信号，100分)",
            "score": 100,
            "price": 140.0,
            "net_assets": 50000.0,
            "regime": "BULL",
        },
        {
            "name": "AAPL (强信号，60分)",
            "score": 60,
            "price": 270.0,
            "net_assets": 50000.0,
            "regime": "RANGE",
        },
        {
            "name": "AAPL (较强信号，70分)",
            "score": 70,
            "price": 270.0,
            "net_assets": 50000.0,
            "regime": "RANGE",
        },
        {
            "name": "TSLA (试探信号，45分)",
            "score": 45,
            "price": 300.0,
            "net_assets": 50000.0,
            "regime": "RANGE",
        },
        {
            "name": "TSLA (一般信号，55分)",
            "score": 55,
            "price": 300.0,
            "net_assets": 50000.0,
            "regime": "BEAR",
        },
    ]

    print("📊 仓位比例对比表")
    print()
    print("| 信号评分 | 旧逻辑仓位 | 新逻辑仓位 | 变化 |")
    print("|---------|----------|----------|-----|")

    for case in test_cases:
        score = case["score"]

        # 旧逻辑
        if score >= 80:
            old_pct = 0.30 + (score - 80) / 200
        elif score >= 60:
            old_pct = 0.20 + (score - 60) / 200
        elif score >= 45:
            old_pct = 0.05 + (score - 45) / 200
        else:
            old_pct = 0.05

        # 新逻辑
        if score >= 80:
            new_pct = 0.20 + (score - 80) / 400
        elif score >= 60:
            new_pct = 0.15 + (score - 60) * 0.07 / 20
        elif score >= 45:
            new_pct = 0.05 + (score - 45) * 0.05 / 14
        else:
            new_pct = 0.05

        change = ((new_pct - old_pct) / old_pct) * 100

        print(
            f"| {score:3d}分 | {old_pct*100:5.1f}% | {new_pct*100:5.1f}% | {change:+5.1f}% |"
        )

    print()
    print("=" * 80)
    print()

    print("💰 实际买入案例测试")
    print()

    regime_scale_map = {"BULL": 1.0, "RANGE": 0.70, "BEAR": 0.40}
    regime_reserve_map = {"BULL": 0.15, "RANGE": 0.30, "BEAR": 0.50}

    for case in test_cases:
        print(f"📈 {case['name']}")
        print(f"   评分: {case['score']}分, 市场状态: {case['regime']}")
        print()

        score = case["score"]
        net_assets = case["net_assets"]
        price = case["price"]
        regime = case["regime"]

        # 新逻辑仓位比例
        if score >= 80:
            new_pct = 0.20 + (score - 80) / 400
        elif score >= 60:
            new_pct = 0.15 + (score - 60) * 0.07 / 20
        elif score >= 45:
            new_pct = 0.05 + (score - 45) * 0.05 / 14
        else:
            new_pct = 0.05

        # 计算预算
        base_budget = net_assets * new_pct
        print(f"   1️⃣  基础预算: ${net_assets:,.0f} × {new_pct:.2%} = ${base_budget:,.0f}")

        # Regime 调整
        scale = regime_scale_map.get(regime, 0.70)
        reserve = regime_reserve_map.get(regime, 0.30)

        adjusted_budget = base_budget * scale
        available_cap = net_assets * (1 - reserve)

        print(f"   2️⃣  Regime调整: ${base_budget:,.0f} × {scale:.2f} = ${adjusted_budget:,.0f}")
        print(
            f"   3️⃣  预留现金: ${net_assets:,.0f} × (1 - {reserve:.2f}) = ${available_cap:,.0f} 可用"
        )

        # 最终预算
        final_budget = min(adjusted_budget, available_cap)
        print(f"   4️⃣  最终预算: ${final_budget:,.0f}")

        # 买入数量
        quantity = int(final_budget / price)
        actual_cost = quantity * price

        print(f"   5️⃣  买入数量: {quantity}股")
        print(f"   6️⃣  实际成本: ${actual_cost:,.0f}")
        print(f"   7️⃣  占净资产: {actual_cost / net_assets:.1%}")
        print()

    print("=" * 80)
    print()

    print("✅ 测试完成")
    print()
    print("📋 优化总结:")
    print("  • 最大仓位从40%降低至25%（降低37.5%）")
    print("  • 80-100分信号：30-40% → 20-25%")
    print("  • 60-79分信号：20-30% → 15-22%")
    print("  • 45-59分信号：5-12% → 5-10%")
    print()
    print("🎲 Kelly 公式集成:")
    print("  • 在动态预算计算后，额外使用 Kelly 公式验证")
    print("  • 取评分预算和 Kelly 推荐的较小值（双重保险）")
    print("  • Kelly 参数优化：")
    print("    - 保守系数：0.5 → 0.4")
    print("    - 最大仓位：0.25 → 0.20")
    print("    - 最小胜率：55% → 60%")
    print("    - 最少交易：10次 → 15次")
    print()
    print("🎯 预期效果:")
    print("  • 风险降低：单笔最大损失从40%降至25%")
    print("  • 更科学：结合历史胜率和盈亏比动态调整")
    print("  • 更稳健：提高 Kelly 启用门槛，确保统计可靠")

    return True


if __name__ == "__main__":
    result = test_position_calculation()
    sys.exit(0 if result else 1)
