#!/usr/bin/env python3
"""检查交易系统状态"""

import sys
import re
from pathlib import Path
from datetime import datetime


def analyze_log_file(log_file):
    """分析日志文件"""
    if not Path(log_file).exists():
        print(f"❌ 日志文件不存在: {log_file}")
        return

    with open(log_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    print(f"📄 分析日志: {log_file}")
    print(f"   总行数: {len(lines)}")
    print()

    # 检查关键状态
    checks = {
        "WebSocket订阅": [
            r"设置实时行情订阅",
            r"成功订阅.*实时行情推送",
            r"WebSocket订阅失败"
        ],
        "信号处理器": [
            r"准备启动信号处理器",
            r"信号处理器任务已创建",
            r"信号处理器正在运行",
            r"启动信号处理器，按优先级"
        ],
        "信号入队": [
            r"实时买入信号入队",
            r"轮询信号入队"
        ],
        "信号接收": [
            r"📥 收到信号",
            r"⏳ 等待信号队列"
        ],
        "信号处理": [
            r"📌 处理交易信号",
            r"处理.*买入信号"
        ],
        "订单提交": [
            r"📤 正在提交订单",
            r"订单提交成功",
            r"订单提交超时",
            r"订单提交失败"
        ],
        "错误": [
            r"ERROR|Exception|❌.*错误|Traceback"
        ]
    }

    print("=" * 60)
    print("状态检查")
    print("=" * 60)

    for category, patterns in checks.items():
        print(f"\n{category}:")
        found = False
        for i, line in enumerate(lines, 1):
            for pattern in patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    # 清理ANSI颜色代码
                    clean_line = re.sub(r'\x1b\[[0-9;]*m', '', line.strip())
                    print(f"  [{i:4d}] {clean_line}")
                    found = True
                    break
        if not found:
            print(f"  ❌ 未找到相关日志")

    # 统计信号
    print("\n" + "=" * 60)
    print("信号统计")
    print("=" * 60)

    signal_enqueued = 0
    signal_received = 0
    signal_processed = 0
    orders_submitted = 0

    for line in lines:
        if "信号入队" in line:
            signal_enqueued += 1
        if "📥 收到信号" in line:
            signal_received += 1
        if "📌 处理交易信号" in line:
            signal_processed += 1
        if "订单提交成功" in line:
            orders_submitted += 1

    print(f"  信号入队: {signal_enqueued}")
    print(f"  信号接收: {signal_received}")
    print(f"  信号处理: {signal_processed}")
    print(f"  订单提交: {orders_submitted}")

    if signal_enqueued > 0 and signal_received == 0:
        print(f"\n  ⚠️ 警告: {signal_enqueued} 个信号入队但没有被接收!")
        print("  可能原因:")
        print("    1. 信号处理器没有启动")
        print("    2. 信号处理器崩溃了")
        print("    3. 队列格式不匹配")

    if signal_received > 0 and signal_processed == 0:
        print(f"\n  ⚠️ 警告: {signal_received} 个信号被接收但没有被处理!")
        print("  可能原因:")
        print("    1. 处理时遇到异常")
        print("    2. 不满足开仓条件")


def main():
    # 查找最新的日志文件
    log_patterns = [
        "trading_*.log",
        "scheduler_*.log",
        "*.log"
    ]

    log_files = []
    for pattern in log_patterns:
        log_files.extend(Path(".").glob(pattern))

    if not log_files:
        print("❌ 没有找到日志文件")
        print("请在包含日志文件的目录运行此脚本")
        sys.exit(1)

    # 按修改时间排序，取最新的
    log_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

    print("=" * 60)
    print("交易系统状态诊断")
    print("=" * 60)
    print()

    # 分析最新日志
    analyze_log_file(log_files[0])

    # 如果有多个日志文件，列出来
    if len(log_files) > 1:
        print("\n" + "=" * 60)
        print("其他日志文件:")
        print("=" * 60)
        for log_file in log_files[1:6]:  # 最多显示5个
            mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
            print(f"  {log_file.name} (修改时间: {mtime.strftime('%Y-%m-%d %H:%M:%S')})")


if __name__ == "__main__":
    main()
