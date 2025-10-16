#!/usr/bin/env python3
"""系统清理工具 - 清理重复订单记录和无效数据"""
import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta
sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.persistence.order_manager import OrderManager
from longport_quant.messaging import SignalQueue
from longport_quant.config import get_settings
from sqlalchemy import select, delete
from longport_quant.persistence.models import OrderRecord

async def cleanup_rejected_orders():
    """清理被拒绝的订单记录"""
    order_mgr = OrderManager()

    print("\n=== 1. 清理被拒绝的订单记录 ===")

    async with order_mgr.session_factory() as session:
        # 查询所有Rejected订单
        stmt = select(OrderRecord).where(
            OrderRecord.status == 'Rejected'
        )
        result = await session.execute(stmt)
        rejected_orders = result.scalars().all()

        if not rejected_orders:
            print("✅ 没有被拒绝的订单需要清理")
            return

        print(f"📋 找到 {len(rejected_orders)} 个被拒绝的订单:")
        for order in rejected_orders:
            print(f"  - {order.symbol} {order.side} {order.quantity}股 @ ${order.price:.2f} ({order.created_at})")

        # 询问是否删除
        confirm = input("\n⚠️  是否删除这些记录? (yes/no): ")
        if confirm.lower() == 'yes':
            stmt = delete(OrderRecord).where(OrderRecord.status == 'Rejected')
            result = await session.execute(stmt)
            await session.commit()
            print(f"✅ 已删除 {result.rowcount} 条被拒绝的订单记录")
        else:
            print("❌ 取消删除")

async def cleanup_old_orders(days=7):
    """清理N天前的旧订单"""
    order_mgr = OrderManager()

    print(f"\n=== 2. 清理 {days} 天前的旧订单 ===")

    cutoff_date = datetime.now() - timedelta(days=days)

    async with order_mgr.session_factory() as session:
        # 查询旧订单
        stmt = select(OrderRecord).where(
            OrderRecord.created_at < cutoff_date
        )
        result = await session.execute(stmt)
        old_orders = result.scalars().all()

        if not old_orders:
            print(f"✅ 没有 {days} 天前的订单需要清理")
            return

        print(f"📋 找到 {len(old_orders)} 个旧订单 (早于 {cutoff_date.date()})")

        confirm = input(f"\n⚠️  是否删除这些记录? (yes/no): ")
        if confirm.lower() == 'yes':
            stmt = delete(OrderRecord).where(OrderRecord.created_at < cutoff_date)
            result = await session.execute(stmt)
            await session.commit()
            print(f"✅ 已删除 {result.rowcount} 条旧订单记录")
        else:
            print("❌ 取消删除")

async def cleanup_redis_queues():
    """清理Redis队列（慎用）"""
    print("\n=== 3. Redis队列清理 ===")

    settings = get_settings()
    signal_queue = SignalQueue(
        redis_url=settings.redis_url,
        queue_key=settings.signal_queue_key,
        processing_key=settings.signal_processing_key,
        failed_key=settings.signal_failed_key,
    )

    stats = await signal_queue.get_stats()

    print(f"📊 当前队列状态:")
    print(f"  - 待处理: {stats['queue_size']}")
    print(f"  - 处理中: {stats['processing_size']}")
    print(f"  - 失败队列: {stats['failed_size']}")

    if stats['queue_size'] + stats['processing_size'] + stats['failed_size'] == 0:
        print("✅ 所有队列都是空的，无需清理")
        await signal_queue.close()
        return

    print("\n⚠️  队列清理选项:")
    print("  1. 清空失败队列 (推荐)")
    print("  2. 清空所有队列 (危险！会丢失待处理信号)")
    print("  0. 跳过")

    choice = input("\n请选择 (0/1/2): ")

    if choice == '1':
        # 清空失败队列
        count = await signal_queue.redis.zcard(signal_queue.failed_key)
        if count > 0:
            await signal_queue.redis.delete(signal_queue.failed_key)
            print(f"✅ 已清空失败队列 ({count} 个信号)")
        else:
            print("✅ 失败队列已经是空的")

    elif choice == '2':
        confirm = input("⚠️⚠️⚠️  确认清空所有队列? 输入 'DELETE ALL' 确认: ")
        if confirm == 'DELETE ALL':
            await signal_queue.redis.delete(signal_queue.queue_key)
            await signal_queue.redis.delete(signal_queue.processing_key)
            await signal_queue.redis.delete(signal_queue.failed_key)
            print("✅ 已清空所有队列")
        else:
            print("❌ 取消清空")
    else:
        print("❌ 跳过队列清理")

    await signal_queue.close()

async def show_duplicate_processes():
    """显示重复的进程"""
    import subprocess

    print("\n=== 4. 检查重复进程 ===")

    # 检查signal_generator
    result = subprocess.run(
        ['ps', 'aux'],
        capture_output=True,
        text=True
    )

    signal_gen_procs = [line for line in result.stdout.split('\n') if 'signal_generator.py' in line and 'grep' not in line]
    order_exec_procs = [line for line in result.stdout.split('\n') if 'order_executor.py' in line and 'grep' not in line]

    print(f"\n📊 signal_generator 进程: {len(signal_gen_procs)} 个")
    for proc in signal_gen_procs:
        parts = proc.split()
        if len(parts) >= 2:
            print(f"  PID: {parts[1]}")

    if len(signal_gen_procs) > 1:
        print("⚠️  警告: 有多个signal_generator进程运行！")

    print(f"\n📊 order_executor 进程: {len(order_exec_procs)} 个")
    for proc in order_exec_procs:
        parts = proc.split()
        if len(parts) >= 2:
            print(f"  PID: {parts[1]}")

    if len(order_exec_procs) > 1:
        print("⚠️  警告: 有多个order_executor进程运行！")
        print("\n建议操作:")
        print("  1. 停止旧进程: ps aux | grep order_executor | grep -v grep | awk '{print $2}' | head -1 | xargs kill")
        print("  2. 确认只剩1个: ps aux | grep order_executor | grep -v grep")

async def main():
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║              系统清理工具 (System Cleanup)                   ║")
    print("╠══════════════════════════════════════════════════════════════╣")
    print("║  功能:                                                         ║")
    print("║  • 清理被拒绝的订单记录                                       ║")
    print("║  • 清理旧订单记录                                             ║")
    print("║  • 清理Redis队列                                              ║")
    print("║  • 检查重复进程                                               ║")
    print("╚══════════════════════════════════════════════════════════════╝")

    try:
        await cleanup_rejected_orders()
        await cleanup_old_orders(days=7)
        await cleanup_redis_queues()
        await show_duplicate_processes()

        print("\n✅ 清理完成！")

    except Exception as e:
        print(f"\n❌ 清理过程出错: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
