#!/usr/bin/env python3
"""
VIXY状态实时监控器
显示系统当前VIXY状态和防御模式配置
"""

import redis
import time
from datetime import datetime
import sys

def monitor_vixy():
    """监控VIXY状态"""
    r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

    print("\n" + "="*60)
    print("🔍 VIXY恐慌模式实时监控")
    print("="*60)

    try:
        while True:
            # 获取VIXY状态
            vixy_price = r.get("market:vixy:price") or "N/A"
            vixy_panic = r.get("market:vixy:panic") == "1"
            vixy_threshold = r.get("market:vixy:threshold") or "30.0"
            vixy_updated = r.get("market:vixy:updated_at") or "N/A"

            # 清屏并显示状态
            print("\033[2J\033[H")  # 清屏
            print("="*60)
            print(f"⏰ 当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("="*60)

            print("\n📊 VIXY状态:")
            print(f"  • 当前价格: ${vixy_price}")
            print(f"  • 恐慌阈值: ${vixy_threshold}")
            print(f"  • 恐慌模式: {'🚨 激活' if vixy_panic else '✅ 正常'}")
            print(f"  • 更新时间: {vixy_updated}")

            if vixy_panic:
                print("\n🛡️ 防御模式已激活:")
                print("  ✅ PG.US (宝洁) - 继续监控")
                print("  ✅ KO.US (可口可乐) - 继续监控")
                print("  ✅ WMT.US (沃尔玛) - 继续监控")
                print("  ✅ COST.US (好市多) - 继续监控")
                print("  ✅ MO.US (奥驰亚) - 继续监控")
                print("  ❌ 其他标的 - 暂停买入")
            else:
                print("\n📈 正常交易模式:")
                print("  • 所有标的正常监控")
                print("  • 防御标的无额外加分")

            # 检查信号队列
            queue_size = r.llen("signal_queue")
            failed_size = r.llen("failed_signal_queue")

            print(f"\n📬 队列状态:")
            print(f"  • 待处理信号: {queue_size}")
            print(f"  • 失败队列: {failed_size}")

            print("\n" + "-"*60)
            print("💡 提示:")
            print("  • VIXY > 30 触发防御模式")
            print("  • 防御标的获得15分评分加成")
            print("  • 按 Ctrl+C 退出监控")

            # 每5秒刷新一次
            time.sleep(5)

    except KeyboardInterrupt:
        print("\n\n✅ 监控已停止")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        sys.exit(1)

if __name__ == "__main__":
    monitor_vixy()