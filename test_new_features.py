#!/usr/bin/env python3
"""
测试新增功能：
1. 实时挪仓功能
2. 紧急度自动卖出
3. TA-Lib 集成
"""

import asyncio
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from longport_quant.config import get_settings
from longport_quant.features.technical_indicators import TechnicalIndicators, TALIB_AVAILABLE
import numpy as np


def test_talib_integration():
    """测试 TA-Lib 集成"""
    print("\n" + "="*70)
    print("测试 1: TA-Lib 集成")
    print("="*70)

    # 测试数据
    closes = np.random.random(100) * 100 + 50

    try:
        # 测试 EMA
        ema12 = TechnicalIndicators.ema(closes, 12)
        ema26 = TechnicalIndicators.ema(closes, 26)

        print(f"✅ EMA(12) 计算成功: 最后值 = {ema12[-1]:.2f}")
        print(f"✅ EMA(26) 计算成功: 最后值 = {ema26[-1]:.2f}")

        # 测试 SMA
        sma20 = TechnicalIndicators.sma(closes, 20)
        print(f"✅ SMA(20) 计算成功: 最后值 = {sma20[-1]:.2f}")

        # 测试 RSI
        rsi = TechnicalIndicators.rsi(closes, 14)
        print(f"✅ RSI(14) 计算成功: 最后值 = {rsi[-1]:.2f}")

        # 测试 MACD
        macd_result = TechnicalIndicators.macd(closes)
        print(f"✅ MACD 计算成功: MACD = {macd_result['macd'][-1]:.2f}, Signal = {macd_result['signal'][-1]:.2f}")

        print(f"\n🎉 TA-Lib 状态: {'已启用' if TALIB_AVAILABLE else '未安装（使用 numpy 降级）'}")

        return True
    except Exception as e:
        print(f"❌ TA-Lib 测试失败: {e}")
        return False


def test_config_loading():
    """测试配置加载"""
    print("\n" + "="*70)
    print("测试 2: 配置加载")
    print("="*70)

    try:
        settings = get_settings()

        # 测试实时挪仓配置
        print("\n实时挪仓配置:")
        print(f"  - REALTIME_ROTATION_ENABLED: {settings.realtime_rotation_enabled}")
        print(f"  - REALTIME_ROTATION_MIN_SIGNAL_SCORE: {settings.realtime_rotation_min_signal_score}")
        print(f"  - REALTIME_ROTATION_MIN_SCORE_DIFF: {settings.realtime_rotation_min_score_diff}")
        print(f"  - REALTIME_ROTATION_MAX_POSITIONS: {settings.realtime_rotation_max_positions}")

        # 测试紧急卖出配置
        print("\n紧急卖出配置:")
        print(f"  - URGENT_SELL_ENABLED: {settings.urgent_sell_enabled}")
        print(f"  - URGENT_SELL_THRESHOLD: {settings.urgent_sell_threshold}")
        print(f"  - URGENT_SELL_COOLDOWN: {settings.urgent_sell_cooldown}秒")

        # 测试时区轮换配置
        print("\n时区轮换配置:")
        print(f"  - TIMEZONE_ROTATION_ENABLED: {settings.timezone_rotation_enabled}")
        print(f"  - HK_FORCE_ROTATION_ENABLED: {settings.hk_force_rotation_enabled}")
        print(f"  - HK_FORCE_ROTATION_MAX: {settings.hk_force_rotation_max}")

        print("\n✅ 所有配置加载成功！")
        return True
    except Exception as e:
        print(f"❌ 配置加载失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_signal_generator_import():
    """测试 signal_generator 导入"""
    print("\n" + "="*70)
    print("测试 3: Signal Generator 导入")
    print("="*70)

    try:
        # 尝试导入主模块
        import scripts.signal_generator as sg

        print("✅ signal_generator.py 导入成功")

        # 检查关键函数是否存在
        has_realtime_rotation = hasattr(sg.SignalGenerator, 'check_realtime_rotation')
        has_urgent_sells = hasattr(sg.SignalGenerator, 'check_urgent_sells')
        has_pre_close_rotation = hasattr(sg.SignalGenerator, 'check_pre_close_rotation')

        print(f"\n功能检查:")
        print(f"  - check_realtime_rotation: {'✅' if has_realtime_rotation else '❌'}")
        print(f"  - check_urgent_sells: {'✅' if has_urgent_sells else '❌'}")
        print(f"  - check_pre_close_rotation: {'✅' if has_pre_close_rotation else '❌'}")

        if all([has_realtime_rotation, has_urgent_sells, has_pre_close_rotation]):
            print("\n✅ 所有新功能都已就绪！")
            return True
        else:
            print("\n⚠️ 部分功能缺失")
            return False

    except Exception as e:
        print(f"❌ 导入失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def print_summary(results):
    """打印测试摘要"""
    print("\n" + "="*70)
    print("测试摘要")
    print("="*70)

    total = len(results)
    passed = sum(results.values())

    for test_name, result in results.items():
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{test_name}: {status}")

    print(f"\n总计: {passed}/{total} 测试通过")

    if passed == total:
        print("\n🎉 所有测试通过！系统已就绪。")
        return 0
    else:
        print(f"\n⚠️  有 {total - passed} 个测试失败，请检查日志。")
        return 1


def main():
    """主测试函数"""
    print("\n" + "="*70)
    print("🧪 新功能测试套件")
    print("="*70)

    results = {}

    # 运行测试
    results["TA-Lib 集成"] = test_talib_integration()
    results["配置加载"] = test_config_loading()
    results["Signal Generator"] = test_signal_generator_import()

    # 打印摘要
    return print_summary(results)


if __name__ == "__main__":
    sys.exit(main())
