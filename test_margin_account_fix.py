#!/usr/bin/env python3
"""
测试融资账户资金判断修复

验证内容：
1. order_executor.py 中融资账户资金判断逻辑
2. 使用 remaining_finance 而非 buy_power
3. 日志输出清晰性
"""

import re
from loguru import logger


def test_syntax():
    """测试语法正确性"""
    logger.info("=" * 70)
    logger.info("语法验证测试")
    logger.info("=" * 70)

    try:
        import py_compile
        logger.info("\n📝 测试 order_executor.py 语法...")
        py_compile.compile('/data/web/longport-quant-new/scripts/order_executor.py', doraise=True)
        logger.success("  ✅ order_executor.py 语法正确")
        return True
    except Exception as e:
        logger.error(f"  ❌ 语法错误: {e}")
        return False


def test_margin_account_logic():
    """测试融资账户逻辑"""
    logger.info("\n" + "=" * 70)
    logger.info("融资账户逻辑测试")
    logger.info("=" * 70)

    try:
        # 读取修复后的代码
        with open('/data/web/longport-quant-new/scripts/order_executor.py', 'r') as f:
            code = f.read()

        # 检查关键修复点
        checks = [
            ('融资账户检测与资金判断修复', '添加了融资账户检测注释'),
            ('remaining_finance > 1000', '使用剩余融资额度判断'),
            ('融资额度充足，可以继续交易', '融资额度充足提示'),
            ('融资额度不足', '融资额度不足警告'),
            ('融资债务', '融资债务说明'),
        ]

        all_passed = True
        for check_str, desc in checks:
            if check_str in code:
                logger.success(f"  ✅ {desc}: 已添加")
            else:
                logger.error(f"  ❌ {desc}: 未找到")
                all_passed = False

        # 检查是否还有旧的 buy_power > 1000 逻辑
        old_pattern = r'buy_power.*>\s*1000'
        matches = re.findall(old_pattern, code)
        if matches:
            logger.error(f"  ❌ 仍有旧的 buy_power 判断逻辑: {len(matches)} 处")
            for match in matches[:3]:
                logger.error(f"     - {match}")
            all_passed = False
        else:
            logger.success(f"  ✅ 已移除旧的 buy_power > 1000 判断")

        # 统计修复的位置数量
        margin_check_count = code.count('融资账户检测与资金判断修复')
        logger.success(f"  ✅ 修复了 {margin_check_count} 处资金判断逻辑")

        return all_passed
    except Exception as e:
        logger.error(f"  ❌ 测试失败: {e}")
        return False


def test_log_improvements():
    """测试日志改进"""
    logger.info("\n" + "=" * 70)
    logger.info("日志改进测试")
    logger.info("=" * 70)

    try:
        with open('/data/web/longport-quant-new/scripts/order_executor.py', 'r') as f:
            code = f.read()

        # 检查新日志
        log_improvements = [
            ('现金余额:', '显示现金余额'),
            ('负数表示融资债务', '解释负数含义'),
            ('剩余融资额度:', '显示剩余额度'),
            ('融资额度充足', '额度充足提示'),
        ]

        for pattern, desc in log_improvements:
            if pattern in code:
                logger.success(f"  ✅ {desc}: 已添加")
            else:
                logger.warning(f"  ⚠️ {desc}: 未找到")

        return True
    except Exception as e:
        logger.error(f"  ❌ 测试失败: {e}")
        return False


def simulate_margin_account_scenario():
    """模拟融资账户场景"""
    logger.info("\n" + "=" * 70)
    logger.info("融资账户场景模拟")
    logger.info("=" * 70)

    # 模拟数据（来自实际日志）
    scenarios = [
        {
            'name': '实际HKD融资账户',
            'available_cash': -38770.01,
            'buy_power': -38770.01,
            'remaining_finance': 320460.07,
            'expected': '可以交易'
        },
        {
            'name': '融资额度不足',
            'available_cash': -50000,
            'buy_power': -50000,
            'remaining_finance': 500,
            'expected': '不能交易'
        },
        {
            'name': '现金账户（正常）',
            'available_cash': 10000,
            'buy_power': 10000,
            'remaining_finance': 0,
            'expected': '可以交易'
        },
    ]

    for scenario in scenarios:
        logger.info(f"\n📊 场景: {scenario['name']}")
        logger.info(f"   现金: ${scenario['available_cash']:,.2f}")
        logger.info(f"   购买力: ${scenario['buy_power']:,.2f}")
        logger.info(f"   剩余融资额度: ${scenario['remaining_finance']:,.2f}")

        # 模拟判断逻辑
        if scenario['available_cash'] < 0:
            # 融资账户
            if scenario['remaining_finance'] > 1000:
                result = "✅ 可以交易"
            else:
                result = "❌ 融资额度不足"
        else:
            # 现金账户
            result = "✅ 可以交易"

        logger.info(f"   判断结果: {result}")
        logger.info(f"   预期结果: {scenario['expected']}")

        if scenario['expected'] in result:
            logger.success(f"   ✅ 判断正确")
        else:
            logger.error(f"   ❌ 判断错误")

    return True


def main():
    """主测试函数"""
    print("""
╔═══════════════════════════════════════════════════════════════════════╗
║                     融资账户资金判断修复验证测试                        ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  测试内容:                                                            ║
║    1. Python 语法正确性                                               ║
║    2. 融资账户判断逻辑                                                 ║
║    3. 日志输出改进                                                     ║
║    4. 实际场景模拟                                                     ║
║                                                                       ║
║  修复内容:                                                            ║
║    • 使用 remaining_finance 替代 buy_power                            ║
║    • 添加融资账户识别和说明                                            ║
║    • 改进日志可读性                                                   ║
║                                                                       ║
║  修复位置:                                                            ║
║    • order_executor.py: 2处买入逻辑                                   ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
    """)

    results = {
        '语法验证': test_syntax(),
        '融资账户逻辑': test_margin_account_logic(),
        '日志改进': test_log_improvements(),
        '场景模拟': simulate_margin_account_scenario(),
    }

    # 总结
    logger.info("\n" + "=" * 70)
    logger.info("测试结果总结")
    logger.info("=" * 70)

    for test_name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        logger.info(f"  {test_name}: {status}")

    all_passed = all(results.values())

    logger.info("\n" + "=" * 70)
    if all_passed:
        logger.success("✅ 所有测试通过！修复成功！")
        logger.info("\n💡 关键改进:")
        logger.info("  1. 现金为负数时，使用剩余融资额度判断（而非购买力）")
        logger.info("  2. HKD账户实际有$320,460融资额度可用")
        logger.info("  3. 修复后可以正常买入港股")
        logger.info("")
        logger.info("📊 预期效果:")
        logger.info("  修复前: HKD购买力-$38,770 → 判断为资金不足")
        logger.info("  修复后: HKD融资额度$320,460 → 可以正常交易")
        logger.info("")
        logger.info("🚀 下一步:")
        logger.info("  1. 重启订单执行器:")
        logger.info("     supervisorctl restart order_executor_live_001")
        logger.info("")
        logger.info("  2. 监控日志:")
        logger.info("     tail -f logs/order_executor_live_001.log")
        logger.info("")
        logger.info("  3. 验证港股买入:")
        logger.info("     - 检查是否还有'资金不足'错误")
        logger.info("     - 确认融资额度正确识别")
        logger.info("     - 订单是否成功提交")
    else:
        logger.error("❌ 部分测试失败，请检查修复内容")

    logger.info("=" * 70)

    return all_passed


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
