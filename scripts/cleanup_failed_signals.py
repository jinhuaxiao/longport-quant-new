#!/usr/bin/env python3
"""
清理失败信号队列

用途：
- 清理Redis中堆积的失败信号
- 可选择清理全部或按条件清理（时间、类型、评分等）
"""

import redis
import json
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from longport_quant.config import get_settings


def connect_redis():
    """连接Redis"""
    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)


def analyze_failed_signals(r: redis.Redis, queue_key: str):
    """分析失败信号队列"""
    signals = r.zrange(queue_key, 0, -1, withscores=True)

    if not signals:
        print(f"✅ 队列 {queue_key} 为空")
        return []

    print(f"\n📊 失败信号分析 ({len(signals)}个)")
    print("=" * 80)

    # 分类统计
    by_type = {}
    by_error = {}
    by_symbol = {}
    old_signals = []  # 超过1小时的信号

    beijing_tz = ZoneInfo('Asia/Shanghai')
    now = datetime.now(beijing_tz)

    for signal_json, score in signals:
        try:
            signal = json.loads(signal_json)

            # 按类型统计
            signal_type = signal.get('type', 'UNKNOWN')
            by_type[signal_type] = by_type.get(signal_type, 0) + 1

            # 按错误统计
            error = signal.get('last_error', 'Unknown')[:50]  # 截取前50字符
            by_error[error] = by_error.get(error, 0) + 1

            # 按标的统计
            symbol = signal.get('symbol', 'UNKNOWN')
            by_symbol[symbol] = by_symbol.get(symbol, 0) + 1

            # 检查信号年龄
            queued_at = signal.get('queued_at')
            if queued_at:
                try:
                    queued_time = datetime.fromisoformat(queued_at.replace('Z', '+00:00'))
                    if queued_time.tzinfo is None:
                        queued_time = queued_time.replace(tzinfo=beijing_tz)
                    age_hours = (now - queued_time).total_seconds() / 3600

                    if age_hours > 1:  # 超过1小时
                        old_signals.append({
                            'signal': signal,
                            'score': score,
                            'age_hours': age_hours
                        })
                except Exception:
                    pass
        except Exception as e:
            print(f"  ⚠️ 解析信号失败: {e}")

    # 打印统计
    print("\n📈 按类型分布:")
    for signal_type, count in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
        print(f"  • {signal_type}: {count}个")

    print("\n❌ 按错误类型分布:")
    for error, count in sorted(by_error.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  • {error}: {count}个")

    print("\n📊 按标的分布:")
    for symbol, count in sorted(by_symbol.items(), key=lambda x: x[1], reverse=True)[:15]:
        print(f"  • {symbol}: {count}个")

    print(f"\n⏰ 超过1小时的信号: {len(old_signals)}个")

    return old_signals


def cleanup_signals(
    r: redis.Redis,
    queue_key: str,
    cleanup_type: str = 'all',
    max_age_hours: float = 1.0,
    error_patterns: list = None
):
    """
    清理失败信号

    Args:
        r: Redis连接
        queue_key: 队列键名
        cleanup_type: 清理类型 ('all', 'old', 'by_error')
        max_age_hours: 最大年龄（小时）
        error_patterns: 错误模式列表（模糊匹配）
    """
    signals = r.zrange(queue_key, 0, -1, withscores=True)

    if not signals:
        print(f"✅ 队列 {queue_key} 为空，无需清理")
        return 0

    beijing_tz = ZoneInfo('Asia/Shanghai')
    now = datetime.now(beijing_tz)

    to_remove = []

    for signal_json, score in signals:
        try:
            signal = json.loads(signal_json)
            should_remove = False

            if cleanup_type == 'all':
                should_remove = True

            elif cleanup_type == 'old':
                # 清理超过指定时间的信号
                queued_at = signal.get('queued_at')
                if queued_at:
                    try:
                        queued_time = datetime.fromisoformat(queued_at.replace('Z', '+00:00'))
                        if queued_time.tzinfo is None:
                            queued_time = queued_time.replace(tzinfo=beijing_tz)
                        age_hours = (now - queued_time).total_seconds() / 3600

                        if age_hours > max_age_hours:
                            should_remove = True
                    except Exception:
                        pass

            elif cleanup_type == 'by_error' and error_patterns:
                # 清理特定错误类型的信号
                error = signal.get('last_error', '')
                for pattern in error_patterns:
                    if pattern.lower() in error.lower():
                        should_remove = True
                        break

            if should_remove:
                to_remove.append(signal_json)
                symbol = signal.get('symbol', 'N/A')
                signal_type = signal.get('type', 'N/A')
                error = signal.get('last_error', 'N/A')[:50]
                print(f"  🗑️ 移除: {symbol} ({signal_type}) - {error}")

        except Exception as e:
            print(f"  ⚠️ 处理信号失败: {e}")

    # 批量移除
    if to_remove:
        removed = r.zrem(queue_key, *to_remove)
        print(f"\n✅ 已移除 {removed} 个信号")
        return removed
    else:
        print(f"\n✅ 没有符合条件的信号需要移除")
        return 0


def main():
    """主函数"""
    print("\n" + "=" * 80)
    print("🧹 失败信号清理工具")
    print("=" * 80)

    # 连接Redis
    r = connect_redis()

    # 支持多个账户
    queue_keys = [
        "trading:signals:failed:live_001",
        "trading:signals:failed:paper_001",
    ]

    for queue_key in queue_keys:
        print(f"\n\n{'='*80}")
        print(f"📋 队列: {queue_key}")
        print(f"{'='*80}")

        # 检查队列是否存在
        if not r.exists(queue_key):
            print(f"  ⏭️ 队列不存在，跳过")
            continue

        # 分析队列
        old_signals = analyze_failed_signals(r, queue_key)

        # 询问清理策略
        print("\n\n🛠️ 清理选项:")
        print("  1. 清理全部信号")
        print("  2. 清理超过1小时的信号")
        print("  3. 清理特定错误类型的信号")
        print("  4. 跳过此队列")

        choice = input("\n请选择 (1-4): ").strip()

        if choice == '1':
            confirm = input(f"⚠️ 确认清理全部 {r.zcard(queue_key)} 个信号? (yes/no): ").strip()
            if confirm.lower() == 'yes':
                cleanup_signals(r, queue_key, cleanup_type='all')

        elif choice == '2':
            if old_signals:
                confirm = input(f"⚠️ 确认清理 {len(old_signals)} 个超过1小时的信号? (yes/no): ").strip()
                if confirm.lower() == 'yes':
                    cleanup_signals(r, queue_key, cleanup_type='old', max_age_hours=1.0)
            else:
                print("✅ 没有超过1小时的信号")

        elif choice == '3':
            print("\n常见错误模式:")
            print("  • TypeError")
            print("  • 可买数量为0")
            print("  • 订单被拒绝")
            pattern = input("\n请输入错误模式（模糊匹配）: ").strip()
            if pattern:
                cleanup_signals(r, queue_key, cleanup_type='by_error', error_patterns=[pattern])

        elif choice == '4':
            print("⏭️ 跳过此队列")

        else:
            print("❌ 无效选择")

    print("\n\n✅ 清理完成！")
    r.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断")
    except Exception as e:
        print(f"\n\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
