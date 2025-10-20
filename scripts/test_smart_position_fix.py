#!/usr/bin/env python3
"""测试修复后的智能仓位管理功能"""

import asyncio
from datetime import datetime
from loguru import logger
import numpy as np


class MockAccount:
    """模拟账户状态"""
    def __init__(self, position_count, positions):
        self.position_count = position_count
        self.positions = positions
        self.cash = {"HKD": 50000, "USD": 0}
        self.net_assets = {"HKD": 200000}  # 总资产20万


class MockQuote:
    """模拟行情数据"""
    def __init__(self, symbol, price):
        self.symbol = symbol
        self.last_done = price


async def test_smart_position_management():
    """测试智能仓位管理的各种场景"""

    from advanced_technical_trading import AdvancedTechnicalTrader

    trader = AdvancedTechnicalTrader(use_builtin_watchlist=True)

    logger.info("=" * 70)
    logger.info("测试修复后的智能仓位管理")
    logger.info("=" * 70)

    # 场景1: 满仓状态，弱势持仓遇到强信号
    logger.info("\n📋 场景1: 满仓状态，弱势持仓遇到强信号")
    logger.info("-" * 50)

    # 创建满仓账户（10个持仓）
    positions = {
        "0005.HK": {"quantity": 100, "cost": 52.0, "currency": "HKD"},  # 亏损 -3.8%
        "0388.HK": {"quantity": 50, "cost": 195.0, "currency": "HKD"},   # 盈利 +2.6%
        "0700.HK": {"quantity": 20, "cost": 340.0, "currency": "HKD"},   # 盈利 +2.9%
        "0939.HK": {"quantity": 1000, "cost": 4.8, "currency": "HKD"},   # 亏损 -4.2%
        "1810.HK": {"quantity": 200, "cost": 12.5, "currency": "HKD"},   # 盈利 +4.0%
        "3690.HK": {"quantity": 50, "cost": 88.0, "currency": "HKD"},    # 亏损 -2.3%
        "9618.HK": {"quantity": 30, "cost": 125.0, "currency": "HKD"},   # 盈利 +1.6%
        "9988.HK": {"quantity": 40, "cost": 95.0, "currency": "HKD"},    # 亏损 -2.6%
        "9992.HK": {"quantity": 100, "cost": 18.0, "currency": "HKD"},   # 盈利 +5.6%
        "1929.HK": {"quantity": 200, "cost": 15.0, "currency": "HKD"},   # 亏损 -2.9%
    }

    account = MockAccount(10, positions)

    # 设置当前价格
    trader._last_quotes = [
        MockQuote("0005.HK", 50.0),   # 当前价 50.0
        MockQuote("0388.HK", 200.0),  # 当前价 200.0
        MockQuote("0700.HK", 350.0),  # 当前价 350.0
        MockQuote("0939.HK", 4.6),    # 当前价 4.6
        MockQuote("1810.HK", 13.0),   # 当前价 13.0
        MockQuote("3690.HK", 86.0),   # 当前价 86.0
        MockQuote("9618.HK", 127.0),  # 当前价 127.0
        MockQuote("9988.HK", 92.5),   # 当前价 92.5
        MockQuote("9992.HK", 19.0),   # 当前价 19.0
        MockQuote("1929.HK", 14.56),  # 当前价 14.56
    ]

    # 设置一些持仓的止损止盈
    trader.positions_with_stops = {
        "0939.HK": {"entry_price": 4.8, "stop_loss": 4.5, "take_profit": 5.2, "atr": 0.15},
        "3690.HK": {"entry_price": 88.0, "stop_loss": 84.0, "take_profit": 94.0, "atr": 2.5},
        "1929.HK": {"entry_price": 15.0, "stop_loss": 14.2, "take_profit": 16.0, "atr": 0.4},
    }

    # 创建一个强买入信号
    new_signal = {
        'symbol': '0981.HK',  # 中芯国际
        'type': 'BUY',
        'strength': 50,  # 中等信号（已降低到50，测试新的清理逻辑）
        'atr': 1.2,
        'stop_loss': 28.5,
        'take_profit': 32.0
    }

    logger.info("账户状态:")
    logger.info(f"  持仓数: {account.position_count}/{trader.max_positions}")
    logger.info(f"  现金: HKD ${account.cash['HKD']:,.0f}")

    logger.info("\n当前持仓盈亏:")
    for symbol, pos in positions.items():
        for q in trader._last_quotes:
            if q.symbol == symbol:
                pnl = (q.last_done / pos['cost'] - 1) * 100
                name = trader._get_symbol_name(symbol)
                logger.info(f"  {symbol:8} ({name:8}): {pnl:+6.2f}%")
                break

    logger.info(f"\n新信号: {new_signal['symbol']} ({trader._get_symbol_name(new_signal['symbol'])})")
    logger.info(f"  类型: {new_signal['type']}, 评分: {new_signal['strength']}/100")

    # 测试仓位清理
    logger.info("\n执行智能仓位管理...")

    # Mock _execute_sell to avoid actual trading
    async def mock_execute_sell(symbol, price, position, reason):
        logger.success(f"  ✅ 模拟执行卖出: {symbol} @ ${price:.2f}, 原因: {reason}")
        return True

    # 临时替换执行卖出函数
    original_execute_sell = trader._execute_sell
    trader._execute_sell = mock_execute_sell

    try:
        result = await trader._try_make_room(new_signal, account.__dict__)

        if result:
            logger.success("\n🎉 仓位清理成功！已为新信号腾出空间")
        else:
            logger.info("\n📊 评估后决定保持当前持仓")

    finally:
        trader._execute_sell = original_execute_sell

    # 场景2: 测试不同信号强度的清理决策
    logger.info("\n" + "=" * 70)
    logger.info("📋 场景2: 测试不同信号强度的清理决策")
    logger.info("-" * 50)

    test_signals = [
        {'symbol': '0981.HK', 'type': 'WEAK_BUY', 'strength': 35, 'atr': 1.2},
        {'symbol': '0981.HK', 'type': 'BUY', 'strength': 55, 'atr': 1.2},
        {'symbol': '0981.HK', 'type': 'STRONG_BUY', 'strength': 75, 'atr': 1.2},
    ]

    for signal in test_signals:
        logger.info(f"\n测试信号: {signal['type']}, 评分: {signal['strength']}")

        # 重新设置原始函数
        trader._execute_sell = mock_execute_sell

        try:
            # 添加必要的字段
            signal['stop_loss'] = 28.5
            signal['take_profit'] = 32.0

            result = await trader._try_make_room(signal, account.__dict__)

            if result:
                logger.info(f"  → 决策: 执行清理")
            else:
                logger.info(f"  → 决策: 保持持仓")

        finally:
            trader._execute_sell = original_execute_sell

    # 场景3: 测试改进后的清理条件
    logger.info("\n" + "=" * 70)
    logger.info("📋 场景3: 测试改进后的清理条件")
    logger.info("-" * 50)

    logger.info("清理条件已优化:")
    logger.info("  1. 弱势持仓(评分<30) + 新信号>60分 → 清理")
    logger.info("  2. 亏损>2% + 新信号>50分 → 清理")
    logger.info("  3. 评分差距>20分 → 清理")
    logger.info("  4. 低收益(<2%) + 强买入信号 → 清理")
    logger.info("\n相比之前更积极的清理策略，确保优质信号能获得交易机会")


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════════════╗
║                   智能仓位管理修复测试                                  ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  修复内容:                                                            ║
║    1. 移除了 _try_make_room 开头的重复检查                             ║
║    2. 改进了清理决策逻辑，降低清理门槛                                  ║
║    3. 增强了日志信息，明确区分执行清理和保持持仓                          ║
║    4. 添加了中文名称显示，便于识别                                      ║
║                                                                       ║
║  改进的清理条件:                                                      ║
║    • 弱势持仓(评分<30) + 新信号>60分                                  ║
║    • 亏损>2% + 新信号>50分                                           ║
║    • 评分差距>20分                                                   ║
║    • 低收益(<2%) + 强买入信号                                        ║
║                                                                       ║
║  预期效果:                                                            ║
║    ✅ 正确识别满仓状态                                               ║
║    ✅ 积极为优质信号清理弱势持仓                                       ║
║    ✅ 实际执行卖出订单而不是空操作                                      ║
║    ✅ 清晰的日志显示决策过程                                          ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
    """)

    asyncio.run(test_smart_position_management())