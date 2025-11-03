#!/usr/bin/env python3
"""批量取消历史订单脚本

功能：
- 查询并取消历史GTC订单（撤单前有效）
- 支持预览模式（--dry-run）
- 支持指定保留天数（--keep-days）
- 显示详细的订单信息和取消结果

用法：
    # 预览模式：查看将要取消的订单（不实际执行）
    python scripts/cancel_old_orders.py --dry-run

    # 只保留今日订单，取消所有历史订单
    python scripts/cancel_old_orders.py --keep-days 1

    # 保留最近3天的订单
    python scripts/cancel_old_orders.py --keep-days 3

    # 取消特定标的的历史订单
    python scripts/cancel_old_orders.py --symbol 1398.HK
"""

import asyncio
import sys
import argparse
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import List, Dict, Any

sys.path.append(str(Path(__file__).parent.parent))

from loguru import logger
from longport_quant.config import get_settings
from longport_quant.execution.client import LongportTradingClient
from longport_quant.persistence.order_manager import OrderManager


def print_separator(char="=", length=80):
    """打印分隔线"""
    print(char * length)


def print_order_summary(orders: List[Dict[str, Any]]):
    """打印订单摘要"""
    if not orders:
        print("\n没有找到订单。\n")
        return

    # 按状态分组
    status_groups = {}
    for order in orders:
        status = order['status']
        if status not in status_groups:
            status_groups[status] = []
        status_groups[status].append(order)

    print(f"\n找到 {len(orders)} 个历史订单：\n")

    # 显示按状态分组的统计
    for status, group_orders in sorted(status_groups.items()):
        print(f"  {status}: {len(group_orders)} 个")

    print()


def print_cancelable_orders(orders: List[Dict[str, Any]], cancelable_statuses: List[str]):
    """打印可取消的订单详情"""
    cancelable = [o for o in orders if o['status'] in cancelable_statuses]

    if not cancelable:
        print("没有可取消的订单。\n")
        return

    print(f"\n可取消的订单 ({len(cancelable)} 个)：\n")
    print_separator("-")

    # 按标的分组显示
    symbol_groups = {}
    for order in cancelable:
        symbol = order['symbol']
        if symbol not in symbol_groups:
            symbol_groups[symbol] = []
        symbol_groups[symbol].append(order)

    for symbol, group_orders in sorted(symbol_groups.items()):
        print(f"\n{symbol} ({len(group_orders)} 个订单):")
        for order in group_orders:
            created_at = order.get('created_at', 'N/A')
            if isinstance(created_at, datetime):
                created_at = created_at.strftime('%Y-%m-%d %H:%M:%S')

            print(f"  • {order['order_id'][:16]}... | {order['side']:4s} | "
                  f"{order['quantity']:>6} @ ${order['price']:>8.2f} | "
                  f"{order['status']:15s} | {created_at}")

    print()


def print_cancel_result(result: Dict[str, Any]):
    """打印取消结果"""
    print_separator("=")
    print("\n📊 取消结果统计：\n")
    print(f"  查询到的订单数: {result['total_found']}")
    print(f"  可取消订单数:   {result['cancelable']}")
    print(f"  成功取消:       {result['cancelled']}")
    print(f"  取消失败:       {result['failed']}")
    print()

    if result.get('cancel_result'):
        cancel_result = result['cancel_result']

        if cancel_result.get('failed', 0) > 0:
            print("❌ 取消失败的订单：\n")
            for order_id in cancel_result.get('failed_ids', []):
                error = cancel_result.get('errors', {}).get(order_id, '未知错误')
                print(f"  • {order_id}: {error}")
            print()

    print_separator("=")


async def main():
    parser = argparse.ArgumentParser(
        description='批量取消历史订单',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 预览模式
  %(prog)s --dry-run

  # 只保留今日订单
  %(prog)s --keep-days 1

  # 指定账号（paper_001）
  %(prog)s --account paper_001 --dry-run

  # 保留最近7天的订单
  %(prog)s --keep-days 7

  # 取消特定标的的历史订单
  %(prog)s --symbol 1398.HK --keep-days 1 --account paper_001
        """
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='预览模式，只显示将要取消的订单但不实际执行'
    )

    parser.add_argument(
        '--keep-days',
        type=int,
        default=1,
        help='保留天数（默认1天，即只保留今日订单）'
    )

    parser.add_argument(
        '--symbol',
        type=str,
        default=None,
        help='指定标的代码（可选，如 1398.HK）'
    )

    parser.add_argument(
        '--account',
        type=str,
        default=None,
        help='指定账号ID（可选，如 paper_001、live_001）'
    )

    parser.add_argument(
        '--no-confirm',
        action='store_true',
        help='跳过确认提示，直接执行（危险！）'
    )

    args = parser.parse_args()

    # 配置日志
    logger.remove()  # 移除默认handler
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:8}</level> | <level>{message}</level>",
        level="INFO"
    )

    print_separator("=")
    print(f"{'📋 批量取消历史订单工具':^80}")
    print_separator("=")

    # 显示配置
    cutoff_date = date.today() - timedelta(days=args.keep_days - 1)
    print(f"\n配置：")
    print(f"  账号ID:       {args.account or '默认账号'}")
    print(f"  保留天数:     {args.keep_days} 天")
    print(f"  截止日期:     {cutoff_date.strftime('%Y-%m-%d')} （该日期之前的订单将被取消）")
    print(f"  标的筛选:     {args.symbol or '全部'}")
    print(f"  预览模式:     {'是' if args.dry_run else '否'}")
    print()

    # 初始化（支持指定账号）
    settings = get_settings(account_id=args.account)

    # 显示账号信息
    if settings.account_id:
        logger.info(f"使用账号: {settings.account_id}")

    order_manager = OrderManager()

    async with LongportTradingClient(settings) as client:
        # 定义可取消的订单状态（包括 GTC 条件单常见的 VarietiesNotReported 状态）
        cancelable_statuses = ["New", "PartialFilled", "WaitToNew", "VarietiesNotReported", "NotReported"]

        print("正在查询历史订单...\n")

        # 执行取消操作
        result = await order_manager.cancel_old_orders(
            trade_client=client,
            keep_days=args.keep_days,
            dry_run=args.dry_run,
            cancelable_statuses=cancelable_statuses
        )

        # 显示订单摘要
        print_order_summary(result['orders'])

        # 显示可取消的订单详情
        print_cancelable_orders(result['orders'], cancelable_statuses)

        # 如果是预览模式
        if args.dry_run:
            print_separator("=")
            print("\n✅ 【预览完成】以上是将要取消的订单。")
            print("\n要实际执行取消操作，请运行:")
            print(f"  python {Path(__file__).name} --keep-days {args.keep_days}")
            print()
            print_separator("=")
            return

        # 如果没有可取消的订单
        if result['cancelable'] == 0:
            print("✅ 没有需要取消的订单。\n")
            return

        # 确认提示
        if not args.no_confirm:
            print_separator("=")
            print(f"\n⚠️  警告：即将取消 {result['cancelable']} 个订单！")
            print("\n请仔细确认以上订单列表。此操作不可撤销！\n")

            confirm = input("确认要继续吗？(yes/no): ")
            if confirm.lower() not in ['yes', 'y']:
                print("\n❌ 操作已取消。\n")
                return

        # 显示取消结果
        print_cancel_result(result)

        if result['cancelled'] > 0:
            print(f"\n✅ 成功取消 {result['cancelled']} 个订单！\n")
        else:
            print("\n⚠️  没有订单被取消。\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n❌ 操作已被用户中断。\n")
        sys.exit(1)
    except Exception as e:
        logger.error(f"发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
