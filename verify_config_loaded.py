#!/usr/bin/env python3
"""验证配置是否正确加载"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from src.longport_quant.config import get_settings

print("=" * 70)
print("🔍 验证 .env 配置是否正确加载")
print("=" * 70)
print()

try:
    settings = get_settings()

    print("✅ 配置加载成功！")
    print()

    # 凯利公式配置
    print("🎯 凯利公式配置:")
    print(f"  KELLY_ENABLED           = {settings.kelly_enabled}")
    print(f"  KELLY_FRACTION          = {settings.kelly_fraction}")
    print(f"  KELLY_MAX_POSITION      = {settings.kelly_max_position}")
    print(f"  KELLY_MIN_WIN_RATE      = {settings.kelly_min_win_rate}")
    print(f"  KELLY_MIN_TRADES        = {settings.kelly_min_trades}")
    print(f"  KELLY_LOOKBACK_DAYS     = {settings.kelly_lookback_days}")
    print()

    # 时区轮动配置
    print("🌐 时区轮动配置:")
    print(f"  TIMEZONE_ROTATION_ENABLED        = {settings.timezone_rotation_enabled}")
    print(f"  TIMEZONE_WEAK_THRESHOLD          = {settings.timezone_weak_threshold}")
    print(f"  TIMEZONE_MAX_ROTATION            = {settings.timezone_max_rotation}")
    print(f"  TIMEZONE_MIN_PROFIT_ROTATION     = {settings.timezone_min_profit_rotation}")
    print(f"  TIMEZONE_STRONG_THRESHOLD        = {settings.timezone_strong_threshold}")
    print(f"  TIMEZONE_MIN_HOLDING_HOURS       = {settings.timezone_min_holding_hours}")
    print()

    print("=" * 70)
    print("🎉 所有配置正确加载！系统已准备就绪！")
    print("=" * 70)
    print()
    print("📝 下一步:")
    print("  1. 启动信号生成器: python scripts/signal_generator.py")
    print("  2. 启动订单执行器: python scripts/order_executor.py")
    print("  3. 观察日志输出和通知")
    print()

except Exception as e:
    print(f"❌ 配置加载失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
