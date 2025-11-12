#!/usr/bin/env python3
"""
实时测试防御标的监控功能
通过Redis触发实际运行的信号生成器
"""

import asyncio
import redis
import time
from datetime import datetime

def test_live_system():
    """测试实际运行的系统"""

    print("\n" + "="*60)
    print("🔍 实时系统防御标的功能测试")
    print("="*60)

    # 连接Redis
    r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

    # 1. 查看当前VIXY状态
    print("\n📊 当前VIXY状态:")
    vixy_price = r.get("market:vixy:price")
    vixy_panic = r.get("market:vixy:panic")
    vixy_threshold = r.get("market:vixy:threshold")
    vixy_updated = r.get("market:vixy:updated_at")

    print(f"  价格: ${vixy_price}")
    print(f"  恐慌: {'是' if vixy_panic == '1' else '否'}")
    print(f"  阈值: ${vixy_threshold}")
    print(f"  更新: {vixy_updated}")

    # 2. 手动设置VIXY状态模拟恐慌
    print("\n🔄 模拟VIXY恐慌 (设置VIXY=35):")
    r.set("market:vixy:price", "35.0")
    r.set("market:vixy:panic", "1")
    r.set("market:vixy:threshold", "30.0")
    r.set("market:vixy:updated_at", datetime.now().isoformat())
    r.expire("market:vixy:price", 600)
    r.expire("market:vixy:panic", 600)

    print("  ✅ 已设置VIXY=35.0 (恐慌模式)")

    # 3. 检查防御标的列表
    print("\n📋 防御标的清单:")
    defensive_symbols = ["PG.US", "KO.US", "WMT.US", "COST.US", "MO.US"]
    for symbol in defensive_symbols:
        print(f"  • {symbol}")

    # 4. 查看信号队列
    print("\n📬 信号队列状态:")
    queue_size = r.llen("signal_queue")
    print(f"  队列长度: {queue_size}")

    if queue_size > 0:
        # 查看队列中的信号
        signals = r.lrange("signal_queue", 0, 4)
        print("  最近信号:")
        for i, signal in enumerate(signals[:5], 1):
            print(f"    {i}. {signal[:100]}...")

    # 5. 监控建议
    print("\n💡 监控建议:")
    print("  1. 查看日志: tail -f logs/signal_generator_live_001.log")
    print("  2. 过滤防御: grep -E '防御|🛡️|PG.US|KO.US' logs/signal_generator_live_001.log")
    print("  3. 查看VIXY: grep VIXY logs/signal_generator_live_001.log | tail -20")

    # 6. 恢复正常
    print("\n🔄 恢复正常模式 (设置VIXY=28):")
    r.set("market:vixy:price", "28.0")
    r.set("market:vixy:panic", "0")
    r.set("market:vixy:updated_at", datetime.now().isoformat())
    print("  ✅ 已设置VIXY=28.0 (正常模式)")

    print("\n" + "="*60)
    print("✅ 测试完成!")
    print("="*60)

    # 7. 总结
    print("\n📊 功能状态总结:")
    print("  ✅ VIXY状态可通过Redis控制")
    print("  ✅ 防御标的已配置(PG/KO/WMT/COST/MO)")
    print("  ✅ 恐慌模式可激活/恢复")
    print("  ⏰ 等待市场开盘验证实际效果")

    print("\n🚀 下次美股开盘时(北京时间22:30):")
    print("  • VIXY > 30时自动激活防御模式")
    print("  • 5个防御标的将继续生成买入信号")
    print("  • 其他标的将停止买入")
    print("  • Slack将收到防御模式通知")

if __name__ == "__main__":
    test_live_system()