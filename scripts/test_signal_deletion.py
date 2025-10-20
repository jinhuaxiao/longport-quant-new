#!/usr/bin/env python3
"""
测试信号删除修复

验证 mark_signal_completed() 能够正确删除 processing 队列中的信号
"""

import asyncio
import sys
from pathlib import Path
from loguru import logger

# 添加项目根目录到路径
sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings
from longport_quant.messaging import SignalQueue


async def test_signal_deletion():
    """测试信号删除功能"""
    settings = get_settings()

    signal_queue = SignalQueue(
        redis_url=settings.redis_url,
        queue_key=settings.signal_queue_key,
        processing_key=settings.signal_processing_key,
        failed_key=settings.signal_failed_key,
        max_retries=settings.signal_max_retries
    )

    try:
        print("\n" + "="*70)
        print("🧪 测试信号删除修复")
        print("="*70)

        # 1. 发布测试信号
        print(f"\n📤 发布测试信号...")
        test_signal = {
            "symbol": "TEST.HK",
            "type": "BUY",
            "side": "BUY",
            "score": 50,
            "price": 10.0
        }
        await signal_queue.publish_signal(test_signal)
        print(f"  ✅ 测试信号已发布")

        # 2. 检查主队列
        stats = await signal_queue.get_stats()
        print(f"\n📊 发布后状态:")
        print(f"  主队列: {stats['queue_size']} 个信号")
        print(f"  处理中: {stats['processing_size']} 个信号")

        assert stats['queue_size'] == 1, "主队列应该有1个信号"
        assert stats['processing_size'] == 0, "processing队列应该为空"

        # 3. 消费信号
        print(f"\n📥 消费信号...")
        signal = await signal_queue.consume_signal(auto_recover=False)
        assert signal is not None, "应该能消费到信号"
        print(f"  ✅ 成功消费信号: {signal.get('symbol')}")

        # 验证_original_json字段存在
        assert '_original_json' in signal, "signal应该包含_original_json字段"
        print(f"  ✅ _original_json字段存在")

        # 4. 检查队列状态（应该移到processing）
        stats = await signal_queue.get_stats()
        print(f"\n📊 消费后状态:")
        print(f"  主队列: {stats['queue_size']} 个信号")
        print(f"  处理中: {stats['processing_size']} 个信号")

        assert stats['queue_size'] == 0, "主队列应该为空"
        assert stats['processing_size'] == 1, "processing队列应该有1个信号"

        # 5. 标记完成（这是关键测试！）
        print(f"\n✅ 标记信号完成...")
        result = await signal_queue.mark_signal_completed(signal)
        assert result, "标记完成应该成功"
        print(f"  ✅ 标记完成成功")

        # 6. 验证信号已从processing队列删除
        stats = await signal_queue.get_stats()
        print(f"\n📊 完成后状态:")
        print(f"  主队列: {stats['queue_size']} 个信号")
        print(f"  处理中: {stats['processing_size']} 个信号")

        if stats['processing_size'] == 0:
            print(f"\n" + "="*70)
            print(f"✅ 测试通过！信号已成功从processing队列删除")
            print(f"="*70)
            print(f"\n💡 修复验证:")
            print(f"  ✅ _original_json字段正确保存")
            print(f"  ✅ mark_signal_completed()使用原始JSON删除")
            print(f"  ✅ processing队列中的信号被正确清理")
            print(f"\n  🎉 Bug已彻底修复！")
            return True
        else:
            print(f"\n" + "="*70)
            print(f"❌ 测试失败！信号仍在processing队列中")
            print(f"="*70)
            return False

    except Exception as e:
        logger.error(f"❌ 测试失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False
    finally:
        await signal_queue.close()


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════╗
║           测试信号删除修复 (Test Signal Deletion Fix)        ║
╠══════════════════════════════════════════════════════════════╣
║  测试内容:                                                     ║
║  • 验证_original_json字段保存                                 ║
║  • 验证mark_signal_completed正确删除信号                      ║
║  • 验证processing队列清理                                     ║
╚══════════════════════════════════════════════════════════════╝
    """)
    success = asyncio.run(test_signal_deletion())
    sys.exit(0 if success else 1)
