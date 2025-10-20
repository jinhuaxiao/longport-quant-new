#!/bin/bash
# 监控正在运行的交易脚本的信号处理情况

PID=$(pgrep -f "advanced_technical_trading.py")

if [ -z "$PID" ]; then
    echo "❌ 交易脚本没有运行"
    echo "请先启动: python3 scripts/advanced_technical_trading.py --builtin"
    exit 1
fi

echo "✅ 找到交易脚本进程: PID=$PID"
echo ""
echo "监控关键事件（按Ctrl+C停止）..."
echo "================================================"
echo ""

# 显示进程的stdout/stderr
# 注意：这只在脚本输出到终端时有效
if [ -f "/proc/$PID/fd/1" ]; then
    tail -f "/proc/$PID/fd/1" 2>/dev/null | \
        grep --line-buffered -E "🔔|📥|📌|📤|📨|✅|❌|信号|处理器|ERROR|Exception" | \
        while read line; do
            timestamp=$(date "+%H:%M:%S")
            echo "[$timestamp] $line"
        done
else
    echo "⚠️ 无法直接读取进程输出"
    echo "建议重启脚本并重定向输出:"
    echo ""
    echo "  pkill -f advanced_technical_trading.py"
    echo "  python3 scripts/advanced_technical_trading.py --builtin 2>&1 | tee trading_live.log"
fi
