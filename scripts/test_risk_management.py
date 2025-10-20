#!/usr/bin/env python3
"""测试改进的风险管理系统"""

import asyncio
from datetime import datetime
from loguru import logger

# 模拟账户和信号数据进行测试
test_scenarios = [
    {
        "name": "资金充足，强信号",
        "account": {
            "cash": {"HKD": 100000, "USD": 0},
            "positions": {},
            "position_count": 0
        },
        "signal": {
            "symbol": "0700.HK",
            "strength": 75,
            "atr": 5.0,
            "current_price": 350.0
        }
    },
    {
        "name": "资金不足，中等信号",
        "account": {
            "cash": {"HKD": 3500, "USD": 0},
            "positions": {
                "9988.HK": {"quantity": 100, "cost": 85, "currency": "HKD"},
                "1398.HK": {"quantity": 1000, "cost": 5.6, "currency": "HKD"}
            },
            "position_count": 2
        },
        "signal": {
            "symbol": "1929.HK",
            "strength": 45,
            "atr": 0.4,
            "current_price": 14.7
        }
    },
    {
        "name": "高波动股票，极强信号",
        "account": {
            "cash": {"HKD": 50000, "USD": 0},
            "positions": {},
            "position_count": 0
        },
        "signal": {
            "symbol": "9992.HK",
            "strength": 85,
            "atr": 8.0,  # 高波动
            "current_price": 100.0
        }
    },
    {
        "name": "低波动蓝筹，中等信号",
        "account": {
            "cash": {"HKD": 30000, "USD": 0},
            "positions": {
                "0005.HK": {"quantity": 100, "cost": 50, "currency": "HKD"}
            },
            "position_count": 1
        },
        "signal": {
            "symbol": "0388.HK",
            "strength": 55,
            "atr": 2.0,  # 低波动
            "current_price": 200.0
        }
    },
    {
        "name": "满仓状态，弱信号",
        "account": {
            "cash": {"HKD": 2000, "USD": 0},
            "positions": {
                f"TEST{i}.HK": {"quantity": 100, "cost": 10, "currency": "HKD"}
                for i in range(10)
            },
            "position_count": 10
        },
        "signal": {
            "symbol": "NEW.HK",
            "strength": 35,
            "atr": 1.0,
            "current_price": 50.0
        }
    }
]


class RiskManagementTester:
    """风险管理测试器"""

    def __init__(self):
        # 复制主系统的参数
        self.max_positions = 10
        self.min_position_size_pct = 0.05
        self.max_position_size_pct = 0.30
        self.min_cash_reserve = 1000
        self.use_adaptive_budget = True

    def _calculate_dynamic_budget(self, account, signal):
        """复制主系统的动态预算计算逻辑"""
        currency = "HKD" if ".HK" in signal.get('symbol', '') else "USD"
        available_cash = account["cash"].get(currency, 0)

        usable_cash = max(0, available_cash - self.min_cash_reserve)

        if usable_cash <= 0:
            return 0

        # 计算账户总价值
        total_portfolio_value = available_cash
        for pos in account["positions"].values():
            position_value = pos.get("quantity", 0) * pos.get("cost", 0)
            if pos.get("currency") == currency:
                total_portfolio_value += position_value

        current_positions = account["position_count"]
        remaining_slots = max(1, self.max_positions - current_positions)

        # 基于账户总价值计算仓位大小
        max_position_value = total_portfolio_value * self.max_position_size_pct
        min_position_value = total_portfolio_value * self.min_position_size_pct

        # 基础预算
        base_budget = usable_cash / remaining_slots if remaining_slots > 0 else 0

        # 信号强度调整
        signal_strength = signal.get('strength', 50)
        if signal_strength >= 80:
            strength_multiplier = 1.5
        elif signal_strength >= 70:
            strength_multiplier = 1.3
        elif signal_strength >= 60:
            strength_multiplier = 1.1
        elif signal_strength >= 50:
            strength_multiplier = 0.9
        elif signal_strength >= 40:
            strength_multiplier = 0.7
        else:
            strength_multiplier = 0.5

        # 波动性调整
        atr = signal.get('atr', 0)
        current_price = signal.get('current_price', 1)
        atr_ratio = (atr / current_price * 100) if current_price > 0 else 0

        if atr_ratio > 8:
            volatility_multiplier = 0.5
        elif atr_ratio > 5:
            volatility_multiplier = 0.7
        elif atr_ratio > 3:
            volatility_multiplier = 0.9
        elif atr_ratio > 1.5:
            volatility_multiplier = 1.0
        else:
            volatility_multiplier = 1.2

        # 计算动态预算
        dynamic_budget = base_budget * strength_multiplier * volatility_multiplier

        # 应用限制
        dynamic_budget = min(dynamic_budget, max_position_value)

        if dynamic_budget < min_position_value:
            if usable_cash < min_position_value:
                dynamic_budget = usable_cash
            else:
                dynamic_budget = min_position_value

        final_budget = min(dynamic_budget, usable_cash)

        return {
            "final_budget": final_budget,
            "available_cash": available_cash,
            "usable_cash": usable_cash,
            "total_portfolio_value": total_portfolio_value,
            "base_budget": base_budget,
            "strength_multiplier": strength_multiplier,
            "volatility_multiplier": volatility_multiplier,
            "atr_ratio": atr_ratio,
            "max_position_value": max_position_value,
            "min_position_value": min_position_value,
            "remaining_slots": remaining_slots
        }


def test_risk_management():
    """测试风险管理系统"""
    logger.info("=" * 70)
    logger.info("测试改进的风险管理系统")
    logger.info("=" * 70)

    tester = RiskManagementTester()

    for i, scenario in enumerate(test_scenarios, 1):
        logger.info(f"\n场景 {i}: {scenario['name']}")
        logger.info("-" * 50)

        account = scenario["account"]
        signal = scenario["signal"]

        # 显示输入条件
        logger.info("📥 输入条件:")
        logger.info(f"  现金: HKD ${account['cash'].get('HKD', 0):,.0f}, USD ${account['cash'].get('USD', 0):,.0f}")
        logger.info(f"  持仓数: {account['position_count']}/{tester.max_positions}")
        logger.info(f"  信号强度: {signal['strength']}/100")
        logger.info(f"  标的: {signal['symbol']} @ ${signal['current_price']:.2f}")
        logger.info(f"  ATR: ${signal['atr']:.2f} ({signal['atr']/signal['current_price']*100:.1f}%)")

        # 计算预算
        result = tester._calculate_dynamic_budget(account, signal)

        # 显示计算结果
        logger.info("\n📊 计算结果:")
        logger.info(f"  账户总值: ${result['total_portfolio_value']:,.0f}")
        logger.info(f"  可用现金: ${result['usable_cash']:,.0f} (扣除${tester.min_cash_reserve}储备)")
        logger.info(f"  剩余仓位: {result['remaining_slots']}个")
        logger.info(f"\n📈 仓位计算:")
        logger.info(f"  基础预算: ${result['base_budget']:,.0f}")
        logger.info(f"  信号强度系数: {result['strength_multiplier']:.1f}x")
        logger.info(f"  波动率系数: {result['volatility_multiplier']:.1f}x")
        logger.info(f"  最小仓位限制: ${result['min_position_value']:,.0f} (总值的5%)")
        logger.info(f"  最大仓位限制: ${result['max_position_value']:,.0f} (总值的30%)")

        logger.info(f"\n💰 最终预算: ${result['final_budget']:,.0f}")

        # 计算可买数量（假设手数为100）
        lot_size = 100
        quantity = int(result['final_budget'] / signal['current_price'] / lot_size) * lot_size
        required = quantity * signal['current_price']

        if quantity > 0:
            logger.info(f"  ✅ 可买入: {quantity}股 (需要${required:,.0f})")
            position_pct = (required / result['total_portfolio_value'] * 100) if result['total_portfolio_value'] > 0 else 0
            logger.info(f"  📊 占总资产比例: {position_pct:.1f}%")
        else:
            logger.info(f"  ❌ 资金不足，无法买入")

        # 风险提示
        logger.info("\n⚠️  风险评估:")
        if result['final_budget'] < result['min_position_value']:
            logger.info(f"  • 预算低于最小仓位要求")
        if result['atr_ratio'] > 5:
            logger.info(f"  • 高波动标的（ATR {result['atr_ratio']:.1f}%），已降低仓位")
        if signal['strength'] < 45:
            logger.info(f"  • 弱信号（{signal['strength']}/100），已降低仓位")
        if account['position_count'] >= tester.max_positions:
            logger.info(f"  • 已达最大持仓数，无法开新仓")
        if result['usable_cash'] <= 0:
            logger.info(f"  • 现金不足（需保留${tester.min_cash_reserve}储备）")


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════════════╗
║                   智能风险管理系统测试                                  ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  测试内容:                                                            ║
║    • 动态仓位计算                                                     ║
║    • 信号强度调整                                                     ║
║    • 波动性（ATR）调整                                                ║
║    • 资金不足处理                                                     ║
║    • 最小/最大仓位限制                                                 ║
║                                                                       ║
║  改进特点:                                                            ║
║    ✅ 不再使用固定预算金额                                            ║
║    ✅ 根据账户总价值动态计算                                           ║
║    ✅ 智能调整仓位大小                                                ║
║    ✅ 保留紧急储备金                                                  ║
║    ✅ 多维度风险评估                                                  ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
    """)

    test_risk_management()