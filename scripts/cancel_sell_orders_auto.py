#!/usr/bin/env python3
"""自动取消所有待执行的SELL订单（无需确认）

紧急使用：直接运行即可取消所有待执行的SELL订单
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings
from longport_quant.execution.client import LongportTradingClient
from loguru import logger


async def main():
    """自动取消所有待执行的SELL订单"""
    account_id = sys.argv[1] if len(sys.argv) > 1 else "paper_001"
    settings = get_settings(account_id=account_id)

    print(f"\n{'='*70}")
    print(f"自动取消SELL订单 - 账户: {account_id}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    async with LongportTradingClient(settings) as client:
        # 获取今日订单
        print("正在获取今日订单...")
        orders = await client.today_orders()

        # 筛选可取消的SELL订单
        cancelable_statuses = [
            "New", "PartialFilled", "WaitToNew",
            "VarietiesNotReported", "NotReported"
        ]

        sell_orders = []
        for o in orders:
            side_str = str(o.side)
            status_str = str(o.status).replace("OrderStatus.", "")

            if "Sell" in side_str and status_str in cancelable_statuses:
                sell_orders.append(o)

        print(f"\n发现 {len(orders)} 个今日订单")
        print(f"其中 {len(sell_orders)} 个待执行的SELL订单\n")

        if not sell_orders:
            print("✅ 没有待执行的SELL订单，无需取消。\n")
            return

        # 显示订单
        print("待取消订单：")
        for i, o in enumerate(sell_orders, 1):
            status = str(o.status).replace("OrderStatus.", "")
            price = float(o.price) if hasattr(o, 'price') and o.price else 0
            print(f"  {i}. {o.symbol:<12} {o.quantity:>6}股 @${price:>8.2f} [{status}]")

        # 直接取消
        print(f"\n⚠️  开始取消 {len(sell_orders)} 个订单...\n")

        order_ids = [o.order_id for o in sell_orders]

        try:
            result = await client.cancel_orders_batch(
                order_ids=order_ids,
                continue_on_error=True
            )

            print(f"{'='*70}")
            print(f"取消完成")
            print(f"{'='*70}")
            print(f"✅ 成功: {result['succeeded']}")
            print(f"❌ 失败: {result['failed']}")
            print(f"📊 总计: {result['total']}")

            if result['failed'] > 0:
                print(f"\n失败详情：")
                for oid, error in result.get('errors', {}).items():
                    # 找到对应的symbol
                    symbol = "Unknown"
                    for o in sell_orders:
                        if o.order_id == oid:
                            symbol = o.symbol
                            break
                    print(f"  ✗ {symbol} ({oid[:16]}...)")
                    print(f"    {error}")

            if result['succeeded'] > 0:
                print(f"\n✅ 已成功取消 {result['succeeded']} 个订单")
                print(f"   新的去杠杆订单将使用限价单，避免跳空风险。")

            print(f"\n{'='*70}\n")

        except Exception as e:
            print(f"\n❌ 批量取消失败: {e}\n")
            raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  操作被中断\n")
    except Exception as e:
        logger.exception(f"执行失败: {e}")
        sys.exit(1)
