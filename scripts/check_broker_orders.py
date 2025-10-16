#!/usr/bin/env python3
"""检查券商今日订单"""
import asyncio
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings
from longport_quant.execution.client import LongportTradingClient

async def main():
    settings = get_settings()

    async with LongportTradingClient(settings) as client:
        # 获取今日订单
        orders = await client.today_orders()

        # 过滤3690.HK的卖单
        sell_orders = [o for o in orders if o.symbol == '3690.HK' and str(o.side) == 'OrderSide.Sell']

        print(f"\n=== 3690.HK 今日卖单 ({len(sell_orders)}个) ===\n")
        for order in sell_orders:
            print(f"订单ID: {order.order_id}")
            print(f"  状态: {order.status}")
            print(f"  数量: {order.quantity}")
            print(f"  价格: ${float(order.price) if order.price else 0:.2f}")
            print(f"  时间: {order.submitted_at if hasattr(order, 'submitted_at') else 'N/A'}")
            print()

if __name__ == "__main__":
    asyncio.run(main())
