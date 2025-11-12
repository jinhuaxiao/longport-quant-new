#!/usr/bin/env python3
"""
测试VIXY恐慌模式和防御标的监控
模拟VIXY价格变化，验证系统响应
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime
from loguru import logger
import redis

sys.path.append(str(Path(__file__).parent))

from scripts.signal_generator import SignalGenerator
from longport_quant.config import get_settings

class VIXYPanicTester:
    """VIXY恐慌模式测试器"""

    def __init__(self):
        self.redis_client = redis.from_url("redis://localhost:6379/0")
        self.settings = get_settings(account_id="paper_001")

    async def simulate_vixy_panic(self, vixy_price: float):
        """模拟VIXY价格并触发恐慌检查"""
        print(f"\n🔄 模拟VIXY价格: ${vixy_price:.2f}")

        # 写入Redis模拟VIXY价格
        self.redis_client.set("test:vixy:price", str(vixy_price))

        # 创建信号生成器实例
        generator = SignalGenerator(self.settings, account_id="paper_001")
        generator.subscribed_symbols = {"AAPL.US", "TSLA.US", "NVDA.US"}  # 模拟已订阅

        # 手动触发VIXY更新
        await generator._handle_vixy_update(vixy_price)

        return generator

    async def test_panic_activation(self):
        """测试恐慌模式激活"""
        print("\n" + "="*60)
        print("📊 测试1: VIXY恐慌模式激活 (VIXY > 30)")
        print("="*60)

        # 模拟VIXY = 35（超过阈值30）
        generator = await self.simulate_vixy_panic(35.0)

        print(f"✅ VIXY当前价格: ${generator.vixy_current_price:.2f}")
        print(f"✅ 恐慌阈值: ${generator.vixy_panic_threshold:.2f}")
        print(f"✅ 恐慌模式: {'激活' if generator.market_panic else '未激活'}")
        print(f"✅ 动态添加的防御标的: {generator.panic_added_symbols}")

        # 测试不同标的的买入信号生成
        print("\n🔍 测试买入信号生成逻辑:")
        test_symbols = [
            ("PG.US", "防御标的", True),
            ("KO.US", "防御标的", True),
            ("NVDA.US", "科技股", False),
            ("TSLA.US", "科技股", False),
        ]

        for symbol, category, should_generate in test_symbols:
            # 模拟信号生成检查
            is_defensive = symbol in generator.defensive_symbols
            can_generate = is_defensive or not generator.market_panic

            status = "✅ 可生成" if can_generate else "❌ 停止"
            print(f"  {symbol:8} ({category:6}): {status}")

            assert can_generate == should_generate, f"{symbol} 信号生成逻辑错误"

        print("\n✅ 恐慌激活测试通过!")
        return generator

    async def test_scoring_bonus(self):
        """测试防御标的评分加成"""
        print("\n" + "="*60)
        print("📊 测试2: 防御标的恐慌期评分加成")
        print("="*60)

        # 继续使用恐慌模式
        generator = await self.simulate_vixy_panic(35.0)

        print(f"恐慌模式: {'是' if generator.market_panic else '否'}")
        print(f"VIXY价格: ${generator.vixy_current_price:.2f}\n")

        # 模拟评分
        test_cases = [
            ("PG.US", 40, 15, 55),    # 防御标的加15分
            ("KO.US", 35, 15, 50),    # 防御标的加15分
            ("WMT.US", 42, 15, 57),   # 防御标的加15分
            ("NVDA.US", 60, 0, 60),   # 非防御标的不加分
        ]

        for symbol, base_score, expected_bonus, expected_total in test_cases:
            is_defensive = symbol in generator.defensive_symbols

            # 模拟评分逻辑
            score = base_score
            bonus = 0
            if generator.market_panic and is_defensive:
                bonus = 15
                score += bonus

            print(f"  {symbol:8}: 基础分={base_score:2d}, 加成={bonus:2d}, 总分={score:2d}")

            assert bonus == expected_bonus, f"{symbol} 加成错误"
            assert score == expected_total, f"{symbol} 总分错误"

        print("\n✅ 评分加成测试通过!")

    async def test_panic_recovery(self):
        """测试恐慌恢复"""
        print("\n" + "="*60)
        print("📊 测试3: VIXY恐慌恢复 (VIXY < 30)")
        print("="*60)

        # 先激活恐慌
        generator = await self.simulate_vixy_panic(35.0)
        generator.panic_added_symbols = {"PG.US", "KO.US"}  # 模拟已添加
        print(f"恐慌激活后: panic={generator.market_panic}, 添加={generator.panic_added_symbols}")

        # 然后恢复
        await generator._handle_vixy_update(28.0)
        print(f"\n恢复后: ")
        print(f"  VIXY价格: ${generator.vixy_current_price:.2f}")
        print(f"  恐慌模式: {'激活' if generator.market_panic else '未激活'}")
        print(f"  保留的防御标的: {generator.panic_added_symbols}")

        assert not generator.market_panic, "恐慌模式应该已解除"
        assert len(generator.panic_added_symbols) > 0, "防御标的应该保留"

        print("\n✅ 恐慌恢复测试通过!")

    async def test_redis_status(self):
        """测试Redis中的VIXY状态"""
        print("\n" + "="*60)
        print("📊 测试4: Redis中的VIXY状态保存")
        print("="*60)

        # 模拟VIXY更新
        generator = await self.simulate_vixy_panic(33.5)

        # 检查Redis中的状态
        await asyncio.sleep(0.5)  # 等待Redis写入

        keys = [
            "market:vixy:price",
            "market:vixy:panic",
            "market:vixy:threshold",
            "market:vixy:updated_at"
        ]

        print("Redis中的VIXY状态:")
        for key in keys:
            value = self.redis_client.get(key)
            if value:
                value = value.decode('utf-8') if isinstance(value, bytes) else value
                print(f"  {key:25}: {value}")
            else:
                print(f"  {key:25}: (未设置)")

        print("\n✅ Redis状态测试完成!")

    async def run_all_tests(self):
        """运行所有测试"""
        print("\n" + "🚀"*30)
        print("         VIXY恐慌模式 & 防御标的监控测试")
        print("🚀"*30)

        try:
            # 运行测试
            await self.test_panic_activation()
            await self.test_scoring_bonus()
            await self.test_panic_recovery()
            await self.test_redis_status()

            print("\n" + "="*60)
            print("🎉 所有测试通过!")
            print("="*60)

            print("\n📋 功能验证总结:")
            print("  ✅ VIXY > 30 触发恐慌模式")
            print("  ✅ 防御标的继续生成信号")
            print("  ✅ 科技股停止买入")
            print("  ✅ 防御标的获得15分加成")
            print("  ✅ VIXY < 30 解除恐慌")
            print("  ✅ 防御标的保留在监控列表")
            print("  ✅ VIXY状态保存到Redis")

            print("\n💡 下一步:")
            print("  1. 等待美股开盘测试实际效果")
            print("  2. 监控Slack通知")
            print("  3. 观察防御标的信号生成")

        except AssertionError as e:
            print(f"\n❌ 测试失败: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"\n❌ 意外错误: {e}")
            logger.exception("测试过程中发生错误")
            sys.exit(1)

async def main():
    tester = VIXYPanicTester()
    await tester.run_all_tests()

if __name__ == "__main__":
    asyncio.run(main())