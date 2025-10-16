#!/usr/bin/env python3
"""
止损止盈系统诊断工具

检查止损止盈系统的各个环节是否正常工作
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings
from longport_quant.execution.client import LongportTradingClient
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.persistence.stop_manager import StopLossManager
from longport_quant.persistence.order_manager import OrderManager


async def main():
    """诊断主函数"""
    print("=" * 70)
    print("🔍 止损止盈系统诊断")
    print("=" * 70)
    print()

    settings = get_settings()
    beijing_tz = ZoneInfo('Asia/Shanghai')

    stop_manager = StopLossManager()
    order_manager = OrderManager()

    # 1. 检查当前持仓
    print("📊 步骤1: 检查当前持仓")
    print("-" * 70)

    try:
        async with LongportTradingClient(settings) as trade_client:
            account = await trade_client.get_account()
            positions = account.get("positions", [])

            if not positions or len(positions) == 0:
                print("⚠️ 当前没有持仓")
                print("   说明: 没有持仓，所以不会触发止损止盈检查")
                print()
            else:
                print(f"✅ 当前持仓数量: {len(positions)}")
                print()

                for i, pos in enumerate(positions, 1):
                    symbol = pos['symbol']
                    quantity = pos.get('quantity', 0)
                    cost_price = pos.get('cost_price', 0)
                    market_value = pos.get('market_value', 0)

                    print(f"   {i}. {symbol}")
                    print(f"      持仓数量: {quantity}股")
                    print(f"      成本价: ${cost_price:.2f}")
                    print(f"      市值: ${market_value:.2f}")

                    # 检查是否有止损止盈设置
                    account_id = account.get("account_id", "")
                    stops = await stop_manager.get_position_stops(account_id, symbol)

                    if stops:
                        print(f"      ✅ 已设置止损止盈:")
                        print(f"         止损位: ${stops.get('stop_loss', 0):.2f}")
                        print(f"         止盈位: ${stops.get('take_profit', 0):.2f}")

                        # 获取当前价格
                        try:
                            async with QuoteDataClient(settings) as quote_client:
                                quotes = await quote_client.get_realtime_quote([symbol])
                                if quotes:
                                    current_price = float(quotes[0].last_done)
                                    print(f"         当前价格: ${current_price:.2f}")

                                    # 检查是否应该触发
                                    stop_loss = stops.get('stop_loss', 0)
                                    take_profit = stops.get('take_profit', 0)

                                    if stop_loss and current_price <= stop_loss:
                                        print(f"         🛑 应触发止损! (当前${current_price:.2f} <= 止损${stop_loss:.2f})")
                                    elif take_profit and current_price >= take_profit:
                                        print(f"         🎯 应触发止盈! (当前${current_price:.2f} >= 止盈${take_profit:.2f})")
                                    else:
                                        stop_distance = ((stop_loss - current_price) / current_price * 100) if stop_loss else 0
                                        profit_distance = ((current_price - take_profit) / current_price * 100) if take_profit else 0
                                        print(f"         ✅ 未触发 (距离止损: {abs(stop_distance):.1f}%, 距离止盈: {abs(profit_distance):.1f}%)")
                        except Exception as e:
                            print(f"         ⚠️ 无法获取实时价格: {e}")
                    else:
                        print(f"      ❌ 未设置止损止盈")
                        print(f"         可能原因:")
                        print(f"         1. 这是旧持仓（在系统启动前就持有）")
                        print(f"         2. order_executor保存止损止盈时失败")
                        print(f"         3. 数据库中的记录已过期或被删除")

                    print()

    except Exception as e:
        print(f"❌ 获取账户信息失败: {e}")
        import traceback
        print(traceback.format_exc())
        print()

    # 2. 检查数据库中的止损止盈记录
    print()
    print("📊 步骤2: 检查数据库中的止损止盈记录")
    print("-" * 70)

    try:
        # 直接查询数据库
        from longport_quant.persistence.db import DatabaseClient

        db = DatabaseClient()
        async with db.session_scope() as session:
            from sqlalchemy import select, func
            from longport_quant.persistence.models import PositionStops

            # 查询所有active状态的记录
            query = select(PositionStops).where(
                PositionStops.status == "active"
            ).order_by(PositionStops.created_at.desc())

            result = await session.execute(query)
            stops_records = result.scalars().all()

            if not stops_records:
                print("⚠️ 数据库中没有active状态的止损止盈记录")
                print("   说明: position_stops表为空或所有记录已完成")
                print()
            else:
                print(f"✅ 找到 {len(stops_records)} 条active止损止盈记录")
                print()

                for i, record in enumerate(stops_records, 1):
                    print(f"   {i}. {record.symbol}")
                    print(f"      入场价: ${record.entry_price:.2f}")
                    print(f"      止损位: ${record.stop_loss:.2f}")
                    print(f"      止盈位: ${record.take_profit:.2f}")
                    print(f"      状态: {record.status}")
                    print(f"      创建时间: {record.created_at}")
                    print()

    except Exception as e:
        print(f"❌ 查询数据库失败: {e}")
        import traceback
        print(traceback.format_exc())
        print()

    # 3. 检查signal_generator是否在运行
    print()
    print("📊 步骤3: 检查signal_generator是否在运行")
    print("-" * 70)

    import subprocess
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True
        )

        signal_gen_processes = [
            line for line in result.stdout.split('\n')
            if 'signal_generator.py' in line and 'grep' not in line
        ]

        if signal_gen_processes:
            print(f"✅ signal_generator正在运行 ({len(signal_gen_processes)}个进程)")
            for proc in signal_gen_processes:
                print(f"   {proc}")
        else:
            print("❌ signal_generator未运行")
            print("   说明: signal_generator负责检查止损止盈并生成SELL信号")
            print("   解决: 启动signal_generator")
            print("   命令: python3 scripts/signal_generator.py &")
    except Exception as e:
        print(f"⚠️ 无法检查进程: {e}")

    print()

    # 4. 检查order_executor是否在运行
    print()
    print("📊 步骤4: 检查order_executor是否在运行")
    print("-" * 70)

    try:
        executor_processes = [
            line for line in result.stdout.split('\n')
            if 'order_executor.py' in line and 'grep' not in line
        ]

        if executor_processes:
            print(f"✅ order_executor正在运行 ({len(executor_processes)}个进程)")
            for proc in executor_processes:
                print(f"   {proc}")
        else:
            print("❌ order_executor未运行")
            print("   说明: order_executor负责执行SELL信号")
            print("   解决: 启动order_executor")
            print("   命令: python3 scripts/order_executor.py &")
    except Exception as e:
        print(f"⚠️ 无法检查进程: {e}")

    print()

    # 5. 检查最近的日志
    print()
    print("📊 步骤5: 检查最近的止损止盈相关日志")
    print("-" * 70)

    try:
        import os
        log_dir = Path("logs")

        if log_dir.exists():
            # 查找signal_generator日志
            signal_logs = list(log_dir.glob("signal_generator*.log"))
            if signal_logs:
                latest_log = max(signal_logs, key=os.path.getmtime)
                print(f"📄 最新日志: {latest_log}")
                print()

                # 搜索止损止盈相关的日志
                with open(latest_log, 'r', encoding='utf-8') as f:
                    lines = f.readlines()

                # 查找最近的止损止盈检查
                relevant_lines = []
                for line in lines[-500:]:  # 最近500行
                    if any(keyword in line for keyword in [
                        '检查退出信号', '止损', '止盈', 'check_exit_signals',
                        'STOP_LOSS', 'TAKE_PROFIT', '平仓信号'
                    ]):
                        relevant_lines.append(line.strip())

                if relevant_lines:
                    print("✅ 找到止损止盈相关日志（最近500行）:")
                    print()
                    for line in relevant_lines[-10:]:  # 显示最近10条
                        print(f"   {line}")
                else:
                    print("⚠️ 最近500行日志中没有止损止盈相关记录")
                    print("   可能原因:")
                    print("   1. signal_generator没有调用check_exit_signals()")
                    print("   2. 获取账户信息失败（account为None）")
                    print("   3. 持仓列表为空")
            else:
                print("⚠️ 未找到signal_generator日志文件")
        else:
            print("⚠️ logs目录不存在")

    except Exception as e:
        print(f"❌ 检查日志失败: {e}")

    print()
    print("=" * 70)
    print("📊 诊断总结")
    print("=" * 70)
    print()
    print("✅ 如果所有检查都通过，系统应该正常工作")
    print("❌ 如果有任何检查失败，请按照提示修复")
    print()
    print("常见问题:")
    print("1. 旧持仓没有止损止盈设置 → 需要手动设置或等待重新买入")
    print("2. signal_generator未运行 → 无法检查和生成卖出信号")
    print("3. order_executor未运行 → 无法执行卖出订单")
    print("4. 账户信息获取失败 → 检查API配置和网络")
    print()


if __name__ == "__main__":
    asyncio.run(main())
