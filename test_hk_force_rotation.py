#!/usr/bin/env python3
"""测试港股强制轮换配置"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))

from src.longport_quant.config import get_settings


def test_config():
    """测试配置是否正确加载"""
    print("=" * 70)
    print("测试港股强制轮换配置")
    print("=" * 70)
    print()

    settings = get_settings()

    print("📋 当前配置:")
    print(f"  HK_FORCE_ROTATION_ENABLED: {settings.hk_force_rotation_enabled}")
    print(f"  HK_FORCE_ROTATION_MAX: {settings.hk_force_rotation_max}")
    print()

    # 验证配置
    if settings.hk_force_rotation_enabled:
        print("✅ 港股强制轮换已启用")
        print(f"   港股收盘前（15:30-16:00）将强制卖出最弱的 {settings.hk_force_rotation_max} 个持仓")
    else:
        print("⚠️  港股强制轮换未启用")
        print("   只有评分≥40的弱势持仓才会被卖出")

    print()
    print("=" * 70)
    print()

    # 显示其他相关配置
    print("🔧 相关时区轮换配置:")
    print(f"  时区轮换总开关: {settings.timezone_rotation_enabled}")
    print(f"  弱势持仓阈值: {settings.timezone_weak_threshold}")
    print(f"  强势持仓阈值: {settings.timezone_strong_threshold}")
    print(f"  单次最大轮换比例: {settings.timezone_max_rotation * 100:.0f}%")
    print()

    return True


if __name__ == "__main__":
    result = test_config()
    sys.exit(0 if result else 1)
