#!/usr/bin/env python3
"""测试手数修复功能"""

import asyncio
from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.utils import LotSizeHelper


async def test_lot_size():
    """测试获取股票手数和计算订单数量"""
    print("=" * 60)
    print("测试手数（Lot Size）功能")
    print("=" * 60)

    settings = get_settings()
    helper = LotSizeHelper()

    # 测试标的
    test_symbols = [
        "1398.HK",  # 工商银行 - 通常是500股一手
        "0700.HK",  # 腾讯 - 通常是100股一手
        "AAPL.US",  # 苹果 - 美股1股一手
    ]

    async with QuoteDataClient(settings) as quote_client:
        for symbol in test_symbols:
            print(f"\n测试标的: {symbol}")
            print("-" * 60)

            try:
                # 1. 获取手数
                lot_size = await helper.get_lot_size(symbol, quote_client)
                print(f"  ✓ 交易手数: {lot_size}股/手")

                # 2. 测试不同预算下的订单数量
                test_budgets = [1000, 5000, 10000]
                test_price = 5.0 if ".HK" in symbol else 150.0

                print(f"\n  假设当前价格: ${test_price:.2f}")
                print(f"  {'预算':<10} {'股数':<10} {'手数':<10} {'实际金额':<12}")
                print(f"  {'-'*10} {'-'*10} {'-'*10} {'-'*12}")

                for budget in test_budgets:
                    quantity = helper.calculate_order_quantity(
                        symbol, budget, test_price, lot_size
                    )
                    num_lots = quantity // lot_size if lot_size > 0 else 0
                    actual_cost = quantity * test_price

                    print(f"  ${budget:<9.0f} {quantity:<10} {num_lots:<10} ${actual_cost:<11.2f}")

                print()

            except Exception as e:
                print(f"  ✗ 错误: {e}")

    print("\n" + "=" * 60)
    print("测试完成!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_lot_size())
