#!/usr/bin/env python3
"""查询特定订单的详细信息"""

import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from loguru import logger
from longport_quant.config import get_settings
from longport_quant.execution.client import LongportTradingClient


async def check_order(account_id: str = None, order_id: str = None):
    """查询订单详情"""
    print("=" * 80)
    print(f"{'查询订单详情':^80}")
    print("=" * 80)
    print()

    settings = get_settings(account_id=account_id)

    if settings.account_id:
        print(f"账号ID: {settings.account_id}")
    else:
        print(f"账号ID: 默认账号")

    print()

    async with LongportTradingClient(settings) as client:
        # 1. 查询今日订单
        print("正在查询今日订单...")
        today_orders = await client.today_orders()
        print(f"今日订单总数: {len(today_orders)}\n")

        # 如果指定了订单号，查找该订单
        if order_id:
            print(f"查找订单号: {order_id}\n")
            print("-" * 80)

            # 在今日订单中查找
            found_in_today = False
            for order in today_orders:
                if order.order_id == order_id:
                    found_in_today = True
                    print("✅ 在今日订单中找到：\n")
                    print(f"  订单ID:       {order.order_id}")
                    print(f"  标的:         {order.symbol}")
                    print(f"  方向:         {order.side}")
                    print(f"  数量:         {order.quantity}")
                    print(f"  价格:         ${float(order.price) if order.price else 0:.2f}")
                    print(f"  状态:         {order.status}")
                    print(f"  订单类型:     {order.order_type if hasattr(order, 'order_type') else 'N/A'}")
                    print(f"  有效期:       {order.time_in_force if hasattr(order, 'time_in_force') else 'N/A'}")
                    print(f"  触发价:       ${float(order.trigger_price) if hasattr(order, 'trigger_price') and order.trigger_price else 'N/A'}")
                    print(f"  创建时间:     {order.submitted_at if hasattr(order, 'submitted_at') else 'N/A'}")
                    print(f"  更新时间:     {order.updated_at if hasattr(order, 'updated_at') else 'N/A'}")
                    print()

                    # 判断是否可取消
                    status_str = str(order.status).replace("OrderStatus.", "")
                    cancelable = status_str in ["New", "PartialFilled", "WaitToNew", "VarietiesNotReported", "NotReported"]
                    print(f"  可取消:       {'✅ 是' if cancelable else '❌ 否'} (当前状态: {status_str})")
                    print()
                    break

            if not found_in_today:
                print("❌ 未在今日订单中找到该订单\n")
                print("正在查询历史订单...\n")

                # 查询历史订单
                from datetime import datetime, timedelta
                try:
                    history_orders = await client.history_orders(
                        start_at=datetime.now() - timedelta(days=30),
                        end_at=datetime.now()
                    )

                    found_in_history = False
                    for order in history_orders:
                        if order.order_id == order_id:
                            found_in_history = True
                            print("✅ 在历史订单中找到：\n")
                            print(f"  订单ID:       {order.order_id}")
                            print(f"  标的:         {order.symbol}")
                            print(f"  方向:         {order.side}")
                            print(f"  数量:         {order.quantity}")
                            print(f"  价格:         ${float(order.price) if hasattr(order, 'price') and order.price else 0:.2f}")
                            print(f"  状态:         {order.status}")
                            print(f"  创建时间:     {order.created_at if hasattr(order, 'created_at') else 'N/A'}")
                            print()

                            # 判断是否可取消
                            status_str = str(order.status).replace("OrderStatus.", "")
                            cancelable = status_str in ["New", "PartialFilled", "WaitToNew", "VarietiesNotReported", "NotReported"]
                            print(f"  可取消:       {'✅ 是' if cancelable else '❌ 否'} (当前状态: {status_str})")
                            print()
                            break

                    if not found_in_history:
                        print("❌ 未在历史订单中找到该订单（可能订单号不正确，或订单太旧已被清理）\n")

                except Exception as e:
                    print(f"❌ 查询历史订单失败: {e}\n")

        else:
            # 显示今日订单摘要
            print("今日订单摘要（按状态分组）：\n")
            status_groups = {}
            for order in today_orders:
                status = str(order.status).replace("OrderStatus.", "")
                if status not in status_groups:
                    status_groups[status] = []
                status_groups[status].append(order)

            for status, orders in sorted(status_groups.items()):
                print(f"  {status:20s}: {len(orders):4d} 个")

            print()

            # 显示可取消的订单
            cancelable_orders = [
                o for o in today_orders
                if str(o.status).replace("OrderStatus.", "") in ["New", "PartialFilled", "WaitToNew", "VarietiesNotReported", "NotReported"]
            ]

            if cancelable_orders:
                print(f"\n可取消的订单 ({len(cancelable_orders)} 个)：\n")
                print("-" * 80)
                for order in cancelable_orders[:10]:  # 只显示前10个
                    print(f"  {order.order_id:20s} | {order.symbol:10s} | {str(order.side):20s} | {str(order.status):20s}")
                if len(cancelable_orders) > 10:
                    print(f"  ... 还有 {len(cancelable_orders) - 10} 个")
            else:
                print("\n✅ 没有可取消的订单")

        print()
        print("=" * 80)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="查询订单详情")
    parser.add_argument("--account", type=str, default=None, help="账号ID（如 paper_001）")
    parser.add_argument("--order-id", type=str, default=None, help="订单ID")

    args = parser.parse_args()

    asyncio.run(check_order(account_id=args.account, order_id=args.order_id))
