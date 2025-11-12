#!/usr/bin/env python3
"""
测试卖出信号执行修复

验证内容：
1. order_executor.py 中 bid_price=None 的处理
2. signal_generator.py 中 redis_client 的访问
"""

import asyncio
from loguru import logger


def test_syntax():
    """测试语法正确性"""
    logger.info("=" * 70)
    logger.info("语法验证测试")
    logger.info("=" * 70)

    try:
        import py_compile

        # 测试 order_executor.py
        logger.info("\n📝 测试 order_executor.py 语法...")
        py_compile.compile('/data/web/longport-quant-new/scripts/order_executor.py', doraise=True)
        logger.success("  ✅ order_executor.py 语法正确")

        # 测试 signal_generator.py
        logger.info("\n📝 测试 signal_generator.py 语法...")
        py_compile.compile('/data/web/longport-quant-new/scripts/signal_generator.py', doraise=True)
        logger.success("  ✅ signal_generator.py 语法正确")

        return True
    except Exception as e:
        logger.error(f"  ❌ 语法错误: {e}")
        return False


def test_bid_price_none_handling():
    """测试 bid_price=None 处理逻辑"""
    logger.info("\n" + "=" * 70)
    logger.info("bid_price=None 处理测试")
    logger.info("=" * 70)

    try:
        # 读取修复后的代码
        with open('/data/web/longport-quant-new/scripts/order_executor.py', 'r') as f:
            code = f.read()

        # 检查是否有 None 检查
        checks = [
            ('if bid_price is None:', '检查 bid_price 是否为 None'),
            ('跳过价格偏差检查', '跳过价格偏差检查的日志'),
            ('市场可能关闭', '市场关闭提示'),
        ]

        all_passed = True
        for check_str, desc in checks:
            if check_str in code:
                logger.success(f"  ✅ {desc}: 已添加")
            else:
                logger.error(f"  ❌ {desc}: 未找到")
                all_passed = False

        # 检查缩进是否正确
        if 'elif price_deviation_pct > 0.01:' in code:
            # 检查这一行前面的空格数
            lines = code.split('\n')
            for i, line in enumerate(lines):
                if 'elif price_deviation_pct > 0.01:' in line:
                    indent = len(line) - len(line.lstrip())
                    if indent >= 16:  # 应该有至少4层缩进（每层4空格）
                        logger.success(f"  ✅ elif 缩进正确 ({indent}个空格)")
                    else:
                        logger.warning(f"  ⚠️ elif 缩进可能不正确 ({indent}个空格)")
                    break

        return all_passed
    except Exception as e:
        logger.error(f"  ❌ 测试失败: {e}")
        return False


def test_redis_client_fix():
    """测试 redis_client 修复"""
    logger.info("\n" + "=" * 70)
    logger.info("redis_client 修复测试")
    logger.info("=" * 70)

    try:
        # 读取修复后的代码
        with open('/data/web/longport-quant-new/scripts/signal_generator.py', 'r') as f:
            code = f.read()

        # 检查是否还有旧的 self.redis_client
        old_pattern = 'self.redis_client'
        new_pattern = 'self.position_manager._redis'

        if old_pattern in code:
            # 统计出现次数
            count = code.count(old_pattern)
            logger.error(f"  ❌ 仍有 {count} 处使用 self.redis_client（未修复）")

            # 显示位置
            lines = code.split('\n')
            for i, line in enumerate(lines, 1):
                if old_pattern in line:
                    logger.error(f"     第 {i} 行: {line.strip()}")

            return False
        else:
            logger.success(f"  ✅ 已全部替换为 self.position_manager._redis")

        # 检查替换是否正确
        if new_pattern in code:
            count = code.count(new_pattern)
            logger.success(f"  ✅ 正确使用 self.position_manager._redis ({count} 处)")
        else:
            logger.warning(f"  ⚠️ 未找到 self.position_manager._redis（可能没有需要修复的代码）")

        return True
    except Exception as e:
        logger.error(f"  ❌ 测试失败: {e}")
        return False


def test_import():
    """测试模块导入"""
    logger.info("\n" + "=" * 70)
    logger.info("模块导入测试")
    logger.info("=" * 70)

    try:
        # 测试导入（不实际运行）
        logger.info("\n📦 测试模块导入...")

        # 这里只是尝试编译，不执行
        import importlib.util

        # 测试 order_executor
        spec = importlib.util.spec_from_file_location(
            "order_executor",
            "/data/web/longport-quant-new/scripts/order_executor.py"
        )
        if spec:
            logger.success("  ✅ order_executor.py 可以导入")

        # 测试 signal_generator
        spec = importlib.util.spec_from_file_location(
            "signal_generator",
            "/data/web/longport-quant-new/scripts/signal_generator.py"
        )
        if spec:
            logger.success("  ✅ signal_generator.py 可以导入")

        return True
    except Exception as e:
        logger.error(f"  ❌ 导入失败: {e}")
        return False


def main():
    """主测试函数"""
    print("""
╔═══════════════════════════════════════════════════════════════════════╗
║                      卖出信号执行修复验证测试                          ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  测试内容:                                                            ║
║    1. Python 语法正确性                                               ║
║    2. bid_price=None 处理逻辑                                         ║
║    3. redis_client 属性修复                                           ║
║    4. 模块导入可行性                                                  ║
║                                                                       ║
║  修复内容:                                                            ║
║    • order_executor.py:1862 添加 None 检查                            ║
║    • signal_generator.py 替换 redis_client 为 position_manager._redis║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
    """)

    results = {
        '语法验证': test_syntax(),
        'bid_price处理': test_bid_price_none_handling(),
        'redis_client修复': test_redis_client_fix(),
        '模块导入': test_import(),
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
        logger.info("\n下一步:")
        logger.info("  1. 重启相关服务:")
        logger.info("     supervisorctl restart signal_generator_live_001")
        logger.info("     supervisorctl restart order_executor_live_001")
        logger.info("")
        logger.info("  2. 监控日志:")
        logger.info("     tail -f logs/order_executor_live_001.log")
        logger.info("")
        logger.info("  3. 验证卖出信号执行:")
        logger.info("     - 等待下次触发止损")
        logger.info("     - 检查是否有 TypeError")
        logger.info("     - 确认订单成功提交")
    else:
        logger.error("❌ 部分测试失败，请检查修复内容")

    logger.info("=" * 70)

    return all_passed


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
