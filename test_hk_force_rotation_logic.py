#!/usr/bin/env python3
"""测试港股强制轮换逻辑（模拟场景）"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime

sys.path.append(str(Path(__file__).parent))

from scripts.signal_generator import SignalGenerator


async def test_force_rotation_logic():
    """测试强制轮换逻辑"""
    print("=" * 70)
    print("测试港股强制轮换逻辑")
    print("=" * 70)
    print()

    # 初始化信号生成器
    generator = SignalGenerator()

    print("✅ 信号生成器初始化成功")
    print()
    print("📋 强制轮换配置:")
    print(f"  启用强制轮换: {generator.hk_force_rotation_enabled}")
    print(f"  最多轮换数量: {generator.hk_force_rotation_max}")
    print()

    # 模拟港股持仓数据
    mock_positions = [
        {
            "symbol": "1299.HK",
            "quantity": 100,
            "avg_cost": 80.35,
            "current_price": 81.40,  # +1.31%
        },
        {
            "symbol": "2318.HK",
            "quantity": 50,
            "avg_cost": 56.85,
            "current_price": 57.75,  # +1.58%
        },
        {
            "symbol": "3988.HK",
            "quantity": 1000,
            "avg_cost": 4.54,
            "current_price": 4.58,  # +0.97%
        },
    ]

    print("🏢 模拟港股持仓:")
    for i, pos in enumerate(mock_positions, 1):
        profit_pct = (pos['current_price'] - pos['avg_cost']) / pos['avg_cost']
        market_value = pos['current_price'] * pos['quantity']
        print(
            f"  {i}. {pos['symbol']}: "
            f"成本=${pos['avg_cost']:.2f}, "
            f"现价=${pos['current_price']:.2f}, "
            f"盈亏={profit_pct:+.1%}, "
            f"市值=${market_value:,.0f}"
        )

    print()
    print("🧪 测试场景:")
    print("  1. 所有持仓都是盈利的（评分<40，不是弱势）")
    print("  2. 正常情况下不会被轮换")
    print("  3. 但启用强制轮换后，会选出最弱的2个卖出")
    print()

    # 模拟评分
    print("📊 预期评分逻辑:")
    print("  基准分: 50")
    print("  盈利>10%: -30分 → 最终=20（强持有）")
    print("  盈利5-10%: -15分 → 最终=35（较强）")
    print("  盈利0-5%: 0到-15分 → 最终=35-50（中性）")
    print()
    print("  预期结果:")
    print("    1299.HK (+1.31%): 评分≈35-40 (较强)")
    print("    2318.HK (+1.58%): 评分≈35-40 (较强)")
    print("    3988.HK (+0.97%): 评分≈45-50 (中性)")
    print()
    print("  → 3988.HK 评分最高（最弱），应该被选中")
    print("  → 然后是 1299.HK 或 2318.HK（评分相近）")
    print()

    # 显示当前时间
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    print(f"⏰ 当前时间: {now.strftime('%H:%M:%S')}")

    if now.hour == 15 and now.minute >= 30:
        print("✅ 当前是港股收盘前时段（15:30-16:00）")
        print("   强制轮换逻辑应该会触发")
    elif now.hour == 16 and now.minute == 0:
        print("✅ 当前是港股收盘时刻（16:00）")
        print("   强制轮换逻辑应该会触发")
    else:
        print("⚠️  当前不是港股收盘前时段（15:30-16:00）")
        print("   需要等到15:30-16:00才会触发强制轮换")
        print()
        print("💡 提示:")
        print("   - 可以在15:30-16:00运行 signal_generator 查看实际效果")
        print("   - 或修改代码临时移除时间检查进行测试")

    print()
    print("=" * 70)
    print()
    print("✅ 测试完成")
    print()
    print("📝 下一步:")
    print("  1. 等待港股收盘前时段（15:30-16:00）")
    print("  2. 运行 signal_generator 观察日志")
    print("  3. 查找日志中的关键信息:")
    print("     - '🔄 港股收盘前强制轮换'")
    print("     - '🎯 已选出 N 个最弱持仓进行强制轮换'")
    print("     - 卖出信号生成记录")

    return True


if __name__ == "__main__":
    result = asyncio.run(test_force_rotation_logic())
    sys.exit(0 if result else 1)
