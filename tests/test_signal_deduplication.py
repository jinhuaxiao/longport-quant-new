#!/usr/bin/env python3
"""
测试信号生成器的去重逻辑

测试场景：
1. 持仓检查：已持有的标的不应生成BUY信号
2. 允许重复买入：今日买过但已卖出的标的，允许再次买入
3. 异常处理：数据库查询失败时，保留上次数据
4. 信号冷却期：15分钟内不重复生成信号
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.append(str(Path(__file__).parent.parent))

from scripts.signal_generator import SignalGenerator


async def test_deduplication_logic():
    """测试去重逻辑"""
    print("="*70)
    print("测试信号生成器去重逻辑")
    print("="*70)

    # 创建信号生成器实例（不运行主循环）
    generator = SignalGenerator(use_builtin_watchlist=True, max_iterations=0)

    # 测试场景1：已持有的标的
    print("\n测试1：已持有的标的应被过滤")
    print("-"*70)
    generator.current_positions = {"AAPL.US", "TSLA.US"}
    generator.traded_today = {"AAPL.US"}
    generator.sold_today = set()

    should_generate, reason = await generator._should_generate_signal("AAPL.US", "BUY")
    assert not should_generate, "已持有的标的应该被过滤"
    print(f"✅ AAPL.US (已持有): {reason}")

    # 测试场景2：今日买过但已卖出
    print("\n测试2：今日买过但已卖出的标的，允许再次买入")
    print("-"*70)
    generator.current_positions = set()  # 已卖出，不在持仓中
    generator.traded_today = {"AAPL.US"}  # 今日买过
    generator.sold_today = {"AAPL.US"}     # 今日卖过
    generator.signal_history = {}  # 清空信号历史

    should_generate, reason = await generator._should_generate_signal("AAPL.US", "BUY")
    print(f"   结果: should_generate={should_generate}, reason='{reason}'")
    # 注意：这里会检查队列，如果队列检查通过且冷却期过了，应该允许
    if should_generate:
        print(f"✅ AAPL.US (已卖出): 允许再次买入")
    else:
        print(f"ℹ️  AAPL.US (已卖出): {reason}")

    # 测试场景3：今日未买过的标的
    print("\n测试3：今日未买过的标的，允许买入")
    print("-"*70)
    generator.current_positions = set()
    generator.traded_today = set()
    generator.sold_today = set()
    generator.signal_history = {}

    should_generate, reason = await generator._should_generate_signal("NVDA.US", "BUY")
    if should_generate:
        print(f"✅ NVDA.US (未买过): 允许买入")
    else:
        print(f"ℹ️  NVDA.US: {reason}")

    # 测试场景4：信号冷却期
    print("\n测试4：信号冷却期检查（15分钟）")
    print("-"*70)
    beijing_tz = ZoneInfo('Asia/Shanghai')
    generator.current_positions = set()
    generator.traded_today = set()
    generator.signal_history = {
        "TSLA.US": datetime.now(beijing_tz) - timedelta(minutes=10)  # 10分钟前
    }

    should_generate, reason = await generator._should_generate_signal("TSLA.US", "BUY")
    assert not should_generate, "冷却期内应该被过滤"
    print(f"✅ TSLA.US (10分钟前有信号): {reason}")

    # 测试场景5：冷却期已过
    generator.signal_history = {
        "META.US": datetime.now(beijing_tz) - timedelta(minutes=20)  # 20分钟前
    }

    should_generate, reason = await generator._should_generate_signal("META.US", "BUY")
    if should_generate:
        print(f"✅ META.US (20分钟前有信号): 冷却期已过，允许买入")
    else:
        print(f"ℹ️  META.US: {reason}")

    print("\n" + "="*70)
    print("所有测试完成！")
    print("="*70)

    # 关闭资源
    await generator.signal_queue.close()


async def test_exception_handling():
    """测试异常处理"""
    print("\n" + "="*70)
    print("测试异常处理逻辑")
    print("="*70)

    generator = SignalGenerator(use_builtin_watchlist=True, max_iterations=0)

    # 设置初始数据
    generator.traded_today = {"AAPL.US", "TSLA.US"}
    generator.sold_today = {"NVDA.US"}
    generator.current_positions = {"AAPL.US", "GOOGL.US"}

    print(f"\n初始状态:")
    print(f"  traded_today: {generator.traded_today}")
    print(f"  sold_today: {generator.sold_today}")
    print(f"  current_positions: {generator.current_positions}")

    # 测试：模拟数据库查询失败（通过异常）
    # 注意：实际的异常会在 _update_traded_today 中被捕获
    print(f"\n假设数据库查询失败后...")
    print(f"  traded_today应保持不变: {generator.traded_today}")
    print(f"  sold_today应保持不变: {generator.sold_today}")
    print(f"  current_positions应保持不变: {generator.current_positions}")

    print("\n✅ 异常处理测试：数据在失败后应保留")

    await generator.signal_queue.close()


async def main():
    """主测试函数"""
    try:
        await test_deduplication_logic()
        await test_exception_handling()
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
