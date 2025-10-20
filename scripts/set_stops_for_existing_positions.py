#!/usr/bin/env python3
"""
为现有持仓设置止损止盈

用途：为系统启动前就持有的股票添加止损止盈设置
"""

import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings
from longport_quant.data.quote_client import QuoteDataClient
from longport_quant.execution.client import LongportTradingClient
from longport_quant.persistence.stop_manager import StopLossManager


async def main():
    """主函数"""
    print("=" * 70)
    print("🛠️ 为现有持仓设置止损止盈")
    print("=" * 70)
    print()

    settings = get_settings()
    stop_manager = StopLossManager()

    print("⚠️ 注意: 此工具会为所有没有止损止盈设置的持仓添加默认设置")
    print("   默认止损: -5% (当前价格 × 0.95)")
    print("   默认止盈: +10% (当前价格 × 1.10)")
    print()

    confirm = input("确认继续? (y/N): ").strip().lower()
    if confirm != 'y':
        print("❌ 已取消")
        return

    print()
    print("📊 获取持仓信息...")
    print()

    positions_to_process = []

    # 尝试从API自动获取持仓
    try:
        async with LongportTradingClient(settings) as trade_client:
            account = await trade_client.get_account()
            positions = account.get("positions", [])

            if positions:
                print(f"✅ 从API获取到 {len(positions)} 个持仓:")
                print()
                for i, pos in enumerate(positions, 1):
                    symbol = pos['symbol']
                    quantity = pos.get('quantity', 0)
                    cost_price = pos.get('cost_price', 0)
                    print(f"   {i}. {symbol} - {quantity}股 @ ${cost_price:.2f}")
                    positions_to_process.append(symbol)
                print()
            else:
                print("⚠️ 账户中没有持仓")
                return

    except Exception as e:
        print(f"⚠️ 无法从API获取持仓: {e}")
        print()
        print("切换到手动输入模式...")
        print()
        print("=" * 70)
        print("请输入您的持仓标的（每行一个，输入空行结束）:")
        print("格式示例: 1398.HK  或  AAPL.US")
        print("=" * 70)
        print()

        while True:
            line = input().strip()
            if not line:
                break
            positions_to_process.append(line)

    if not positions_to_process:
        print()
        print("⚠️ 没有需要处理的持仓")
        return

    print()
    print(f"📝 准备为 {len(positions_to_process)} 个持仓设置止损止盈")
    print()

    # 为每个持仓设置止损止盈
    try:
        async with QuoteDataClient(settings) as quote_client:
            success_count = 0
            skip_count = 0
            error_count = 0

            for symbol in positions_to_process:
                try:
                    # 检查是否已有设置
                    existing = await stop_manager.get_stop_for_symbol(symbol)

                    if existing and existing.get('status') == 'active':
                        print(f"⏭️ {symbol}: 已有止损止盈设置，跳过")
                        skip_count += 1
                        continue

                    # 获取当前价格（作为入场价）
                    quotes = await quote_client.get_realtime_quote([symbol])
                    if not quotes:
                        print(f"❌ {symbol}: 无法获取行情，跳过")
                        error_count += 1
                        continue

                    current_price = float(quotes[0].last_done)

                    # 计算止损止盈
                    entry_price = current_price  # 使用当前价格作为入场价
                    stop_loss = entry_price * 0.95  # -5%
                    take_profit = entry_price * 1.10  # +10%

                    # 保存到数据库
                    await stop_manager.save_stop(
                        symbol=symbol,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit
                    )

                    print(f"✅ {symbol}: 已设置")
                    print(f"   入场价: ${entry_price:.2f}")
                    print(f"   止损位: ${stop_loss:.2f} (-5%)")
                    print(f"   止盈位: ${take_profit:.2f} (+10%)")
                    print()

                    success_count += 1

                except Exception as e:
                    print(f"❌ {symbol}: 设置失败 - {e}")
                    error_count += 1
                    continue

        print()
        print("=" * 70)
        print("📊 设置结果")
        print("=" * 70)
        print(f"  ✅ 成功设置: {success_count} 个")
        print(f"  ⏭️ 已有设置: {skip_count} 个")
        print(f"  ❌ 设置失败: {error_count} 个")
        print("=" * 70)
        print()

        if success_count > 0:
            print("✅ 设置完成！signal_generator会在下一轮扫描时开始检查止损止盈")
            print()
            print("验证命令:")
            print("  # 查看数据库记录")
            print("  psql -h 127.0.0.1 -U postgres -d longport_next_new -c \\")
            print("    \"SELECT symbol, entry_price, stop_loss, take_profit, status")
            print("     FROM position_stops WHERE status = 'active'\"")
            print()
            print("  # 监控日志")
            print("  tail -f logs/signal_generator.log | grep -E '止损|止盈|SELL'")

    except Exception as e:
        print(f"❌ 操作失败: {e}")
        import traceback
        print(traceback.format_exc())


if __name__ == "__main__":
    asyncio.run(main())
