#!/usr/bin/env python3
"""检查重复订单"""
import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta
sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.persistence.order_manager import OrderManager

async def main():
    order_mgr = OrderManager()

    # 获取最近1小时的3690.HK订单
    async with order_mgr.session_factory() as session:
        from sqlalchemy import select
        from longport_quant.persistence.models import OrderRecord

        one_hour_ago = datetime.now() - timedelta(hours=1)
        stmt = select(OrderRecord).where(
            OrderRecord.symbol == '3690.HK',
            OrderRecord.side == 'SELL',
            OrderRecord.created_at >= one_hour_ago
        ).order_by(OrderRecord.created_at.desc())

        result = await session.execute(stmt)
        orders = result.scalars().all()

        print(f"\n=== 3690.HK 最近1小时的卖单 ({len(orders)}个) ===\n")
        for order in orders:
            print(f"订单ID: {order.order_id}")
            print(f"  数量: {order.quantity}")
            print(f"  价格: ${order.price:.2f}")
            print(f"  状态: {order.status}")
            print(f"  时间: {order.created_at}")
            print()

if __name__ == "__main__":
    asyncio.run(main())
