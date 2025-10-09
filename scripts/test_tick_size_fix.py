#!/usr/bin/env python3
"""测试港股价格档位修复"""

import asyncio
from loguru import logger


def test_tick_size_adjustment():
    """测试港股价格档位调整"""

    # 从主程序导入交易系统
    from advanced_technical_trading import AdvancedTechnicalTrader

    trader = AdvancedTechnicalTrader()

    logger.info("=" * 70)
    logger.info("测试港股价格档位调整")
    logger.info("=" * 70)

    # 测试不同价格区间的调整
    test_cases = [
        # (原价格, 股票代码, 预期调整后价格)
        (0.123, "TEST.HK", 0.123),      # < 0.25, 档位0.001
        (0.388, "TEST.HK", 0.390),      # 0.25-0.50, 档位0.005
        (5.678, "TEST.HK", 5.68),       # 0.50-10, 档位0.01
        (15.234, "TEST.HK", 15.24),     # 10-20, 档位0.02
        (85.38, "0981.HK", 85.40),      # 20-100, 档位0.05 (这是出错的价格)
        (85.55, "0981.HK", 85.55),      # 20-100, 档位0.05 (正确的价格)
        (85.53, "0981.HK", 85.55),      # 20-100, 档位0.05
        (150.12, "TEST.HK", 150.10),    # 100-200, 档位0.10
        (350.33, "TEST.HK", 350.40),    # 200-500, 档位0.20
        (750.75, "TEST.HK", 751.00),    # 500-1000, 档位0.50
        (1500.60, "TEST.HK", 1501.00),  # 1000-2000, 档位1.00
        (12.34, "AAPL.US", 12.34),      # 美股，保留2位小数
    ]

    logger.info("\n价格档位规则:")
    logger.info("  $0.01 - $0.25:    档位 $0.001")
    logger.info("  $0.25 - $0.50:    档位 $0.005")
    logger.info("  $0.50 - $10.00:   档位 $0.01")
    logger.info("  $10.00 - $20.00:  档位 $0.02")
    logger.info("  $20.00 - $100.00: 档位 $0.05")
    logger.info("  $100.00 - $200.00: 档位 $0.10")
    logger.info("  $200.00 - $500.00: 档位 $0.20")
    logger.info("  $500.00 - $1000.00: 档位 $0.50")
    logger.info("  $1000.00 - $2000.00: 档位 $1.00")

    logger.info("\n测试结果:")
    logger.info("-" * 50)

    for original_price, symbol, expected_price in test_cases:
        adjusted_price = trader._adjust_price_to_tick_size(original_price, symbol)
        status = "✅" if adjusted_price == expected_price else "❌"

        if '.HK' in symbol:
            market = "港股"
        elif '.US' in symbol:
            market = "美股"
        else:
            market = "未知"

        logger.info(
            f"  {status} {symbol:10} ({market}): "
            f"${original_price:.3f} → ${adjusted_price:.3f} "
            f"(预期: ${expected_price:.3f})"
        )

        if adjusted_price != expected_price:
            logger.error(f"     错误：调整结果不正确！")

    # 特别测试问题案例
    logger.info("\n" + "=" * 70)
    logger.info("特别测试: 0981.HK 错误案例")
    logger.info("=" * 70)

    problem_price = 85.38  # 这是导致错误的价格
    correct_price = trader._adjust_price_to_tick_size(problem_price, "0981.HK")

    logger.info(f"\n原始错误价格: ${problem_price:.2f}")
    logger.info(f"调整后价格: ${correct_price:.2f}")
    logger.info(f"价格区间: $20-$100 (档位: $0.05)")

    if correct_price in [85.35, 85.40]:
        logger.success("✅ 价格已正确调整到有效档位！")
    else:
        logger.error("❌ 价格调整仍有问题！")

    # 测试买卖价格计算
    logger.info("\n" + "=" * 70)
    logger.info("测试完整的下单价格计算")
    logger.info("=" * 70)

    test_symbol = "0981.HK"
    current_price = 85.55
    atr = 4.65

    # 买入价格计算
    buy_price = trader._calculate_order_price(
        "BUY",
        current_price,
        bid_price=None,
        ask_price=None,
        atr=atr,
        symbol=test_symbol
    )

    logger.info(f"\n买入订单:")
    logger.info(f"  当前价: ${current_price:.2f}")
    logger.info(f"  ATR: ${atr:.2f}")
    logger.info(f"  计算后下单价: ${buy_price:.2f}")

    # 检查价格是否符合档位
    remainder = (buy_price * 100) % 5  # 检查是否是0.05的倍数
    if remainder == 0:
        logger.success(f"  ✅ 价格符合$0.05档位要求")
    else:
        logger.error(f"  ❌ 价格不符合档位要求")


if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════════════╗
║                   港股价格档位修复测试                                  ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  问题描述:                                                            ║
║    0981.HK下单价格$85.38不符合港股价格档位规则                         ║
║    错误信息: Wrong bid size, please change the price                  ║
║                                                                       ║
║  修复内容:                                                            ║
║    1. 添加_adjust_price_to_tick_size函数处理港股价格档位               ║
║    2. 根据价格区间自动调整到有效档位                                   ║
║    3. 确保下单价格符合交易所规则                                       ║
║                                                                       ║
║  港股价格档位规则:                                                    ║
║    $20-$100区间必须使用$0.05的档位                                    ║
║    如: $85.35, $85.40, $85.45 (✓)                                   ║
║    如: $85.38, $85.42, $85.53 (✗)                                   ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
    """)

    test_tick_size_adjustment()