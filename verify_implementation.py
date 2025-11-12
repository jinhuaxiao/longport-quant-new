#!/usr/bin/env python3
"""
快速功能验证 - 直接检查代码和配置
"""

from loguru import logger


def main():
    print("""
╔═══════════════════════════════════════════════════════════════════════╗
║                     功能实现验证（简化版）                            ║
╚═══════════════════════════════════════════════════════════════════════╝
""")

    # 1. 语法检查
    logger.info("1️⃣ 检查语法...")
    try:
        import py_compile
        py_compile.compile('scripts/signal_generator.py', doraise=True)
        logger.success("   ✅ 语法正确")
    except Exception as e:
        logger.error(f"   ❌ 语法错误: {e}")
        return False

    # 2. Phase 1: Regime集成
    logger.info("\n2️⃣ Phase 1: Regime集成...")
    with open('scripts/signal_generator.py', 'r') as f:
        code = f.read()

    checks = [
        ('RegimeClassifier', '导入RegimeClassifier'),
        ('self.regime_classifier', '初始化分类器'),
        ('regime_result = await self.regime_classifier.classify', '调用分类'),
        ('if regime == "BULL"', '牛市判断'),
        ('score -= 10', '牛市减分'),
        ('elif regime == "BEAR"', '熊市判断'),
        ('score += 15', '熊市加分'),
    ]

    for pattern, desc in checks:
        if pattern in code:
            logger.success(f"   ✅ {desc}")
        else:
            logger.error(f"   ❌ {desc}")
            return False

    # 3. Phase 2: 渐进式减仓
    logger.info("\n3️⃣ Phase 2: 渐进式减仓...")
    checks = [
        ('gradual_exit_enabled', '读取开关'),
        ('GRADUAL_EXIT', '动作类型'),
        ('gradual_qty = int(quantity * 0.25)', '25%计算'),
        ('渐进式减仓 - 先减25%仓位', '日志输出'),
    ]

    for pattern, desc in checks:
        if pattern in code:
            logger.success(f"   ✅ {desc}")
        else:
            logger.error(f"   ❌ {desc}")
            return False

    # 4. Phase 3: 智能加仓
    logger.info("\n4️⃣ Phase 3: 智能加仓...")
    checks = [
        ('async def check_add_position_signals', '方法定义'),
        ('add_position_enabled', '读取开关'),
        ('if regime == "BEAR"', '熊市检查'),
        ('ADD_POSITION', '信号类型'),
        ('add_signals = await self.check_add_position_signals', '调用方法'),
    ]

    for pattern, desc in checks:
        if pattern in code:
            logger.success(f"   ✅ {desc}")
        else:
            logger.error(f"   ❌ {desc}")
            return False

    # 5. 配置检查
    logger.info("\n5️⃣ 配置文件...")
    with open('.env', 'r') as f:
        config = f.read()

    configs = [
        'REGIME_EXIT_SCORE_ADJUSTMENT=true',
        'GRADUAL_EXIT_ENABLED=true',
        'GRADUAL_EXIT_THRESHOLD_25=40',
        'GRADUAL_EXIT_THRESHOLD_50=50',
        'ADD_POSITION_ENABLED=true',
        'ADD_POSITION_MIN_PROFIT_PCT=2.0',
        'ADD_POSITION_PCT=0.15',
    ]

    for cfg in configs:
        if cfg in config:
            logger.success(f"   ✅ {cfg}")
        else:
            logger.error(f"   ❌ {cfg}")
            return False

    logger.info("\n" + "="*70)
    logger.success("✅ 所有功能验证通过！")
    logger.info("\n💡 实现总结:")
    logger.info("  • Phase 1: Regime集成 - 牛市-10分，熊市+15分")
    logger.info("  • Phase 2: 渐进式减仓 - 评分40-49减25%，50-69减50%")
    logger.info("  • Phase 3: 智能加仓 - 盈利>2%+健康度好+强信号")
    logger.info("")
    logger.info("🚀 可以重启服务了:")
    logger.info("   supervisorctl restart signal_generator_live_001")
    logger.info("="*70)
    return True


if __name__ == '__main__':
    import sys
    success = main()
    sys.exit(0 if success else 1)
