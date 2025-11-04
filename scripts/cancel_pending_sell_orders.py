#!/usr/bin/env python3
"""紧急：取消所有待执行的SELL订单

用途：在修复去杠杆市价单风险后，取消之前可能提交的待执行订单
避免开盘时以不利价格（跳空）成交

使用方法：
    python scripts/cancel_pending_sell_orders.py

安全性：
    - 会显示所有待取消订单的详情
    - 需要输入 YES 确认后才会执行
    - 使用批量取消API，自动处理错误
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
    """检查并取消所有待执行的SELL订单"""

    # 获取配置
    account_id = sys.argv[1] if len(sys.argv) > 1 else "paper_001"
    settings = get_settings(account_id=account_id)

    print(f"\n{'='*70}")
    print(f"紧急订单取消工具 - 账户: {account_id}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    async with LongportTradingClient(settings) as client:
        # 获取今日所有订单
        logger.info("正在获取今日订单...")
        orders = await client.today_orders()

        # 筛选可取消的SELL订单
        cancelable_statuses = [
            "New",                    # 新订单
            "PartialFilled",         # 部分成交
            "WaitToNew",             # 等待提交
            "VarietiesNotReported",  # 品种未报告（常见于GTC条件单）
            "NotReported"            # 未报告
        ]

        sell_orders = []
        for o in orders:
            side_str = str(o.side)
            status_str = str(o.status).replace("OrderStatus.", "")

            if "Sell" in side_str and status_str in cancelable_statuses:
                sell_orders.append(o)

        # 显示结果
        print(f"{'='*70}")
        print(f"扫描结果：发现 {len(orders)} 个今日订单")
        print(f"         其中 {len(sell_orders)} 个待执行的SELL订单")
        print(f"{'='*70}\n")

        if not sell_orders:
            print("✅ 太好了！没有发现待执行的SELL订单。")
            print("   说明：")
            print("   - Rebalancer可能还未触发")
            print("   - 或者订单已经成交/取消")
            print("   - 或者系统有其他保护机制生效")
            print("\n无需任何操作。\n")
            return

        # 显示每个订单的详情
        print("待执行的SELL订单详情：\n")
        print(f"{'序号':<4} {'标的':<12} {'数量':<8} {'价格':<12} {'状态':<20} {'订单ID'}")
        print("-" * 70)

        for i, order in enumerate(sell_orders, 1):
            status = str(order.status).replace("OrderStatus.", "")
            price = float(order.price) if hasattr(order, 'price') and order.price else 0
            order_id_short = order.order_id[:16] + "..." if len(order.order_id) > 20 else order.order_id

            print(f"{i:<4} {order.symbol:<12} {order.quantity:<8} "
                  f"${price:<11.2f} {status:<20} {order_id_short}")

        print("-" * 70)

        # 风险提示
        print(f"\n⚠️  风险提示：")
        print(f"   如果这些是去杠杆订单（在非交易时段提交的市价单）：")
        print(f"   - 它们会在开盘时以开盘价成交")
        print(f"   - 可能遭遇1-10%的跳空损失")
        print(f"   - 建议立即取消，在市场开盘后用限价单重新下单")
        print()

        # 确认取消
        print(f"{'='*70}")
        confirm = input(f"⚠️  确认取消以上 {len(sell_orders)} 个SELL订单？(输入 YES 继续，其他退出): ")
        print(f"{'='*70}\n")

        if confirm != 'YES':
            print("❌ 操作已取消，订单保持不变。")
            print("\n如果需要取消订单，请重新运行此脚本并输入 YES 确认。\n")
            return

        # 批量取消订单
        order_ids = [o.order_id for o in sell_orders]
        print(f"正在批量取消 {len(order_ids)} 个订单...\n")

        try:
            result = await client.cancel_orders_batch(
                order_ids=order_ids,
                continue_on_error=True  # 即使部分失败也继续
            )

            # 显示结果
            print(f"{'='*70}")
            print(f"取消结果")
            print(f"{'='*70}")
            print(f"✅ 成功取消: {result['succeeded']} 个订单")
            print(f"❌ 取消失败: {result['failed']} 个订单")
            print(f"📊 总计处理: {result['total']} 个订单")
            print(f"{'='*70}\n")

            # 显示成功的订单
            if result['succeeded'] > 0:
                print(f"成功取消的订单ID：")
                for oid in result.get('success_ids', [])[:10]:  # 最多显示10个
                    print(f"  ✓ {oid}")
                if len(result.get('success_ids', [])) > 10:
                    print(f"  ... 还有 {len(result['success_ids']) - 10} 个")
                print()

            # 显示失败的订单
            if result['failed'] > 0:
                print(f"取消失败的订单：")
                for oid, error in result.get('errors', {}).items():
                    print(f"  ✗ {oid}")
                    print(f"    原因: {error}")
                print()
                print(f"失败原因可能是：")
                print(f"  - 订单已经成交")
                print(f"  - 订单已经被取消")
                print(f"  - 订单类型不支持取消")
                print()

            # 后续建议
            print(f"{'='*70}")
            print(f"后续建议")
            print(f"{'='*70}")
            print(f"1. 运行检查命令验证结果：")
            print(f"   python scripts/check_order_detail.py --account {account_id}")
            print()
            print(f"2. 重启服务应用最新修复：")
            print(f"   ./scripts/manage_accounts.sh restart {account_id}")
            print()
            print(f"3. 新的去杠杆订单将使用限价单，避免跳空风险")
            print(f"{'='*70}\n")

        except Exception as e:
            logger.error(f"批量取消订单失败: {e}")
            print(f"\n❌ 取消失败：{e}")
            print(f"\n请尝试：")
            print(f"1. 检查网络连接")
            print(f"2. 检查API权限")
            print(f"3. 手动通过券商APP取消订单")
            print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  操作被用户中断")
    except Exception as e:
        logger.exception(f"程序执行失败: {e}")
        sys.exit(1)
