#!/bin/bash
#
# 停止解耦交易系统
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_ROOT/logs"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           停止解耦交易系统 (Stop Trading System)             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# 停止信号生成器
if [ -f "$LOG_DIR/signal_generator.pid" ]; then
    echo "⏹️ 停止信号生成器..."
    while read pid; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            echo "   已停止 PID: $pid"
        else
            echo "   PID $pid 已停止"
        fi
    done < "$LOG_DIR/signal_generator.pid"
    rm "$LOG_DIR/signal_generator.pid"
else
    echo "⚠️ 未找到signal_generator.pid文件"
fi

# 停止订单执行器
if [ -f "$LOG_DIR/order_executor.pid" ]; then
    echo "⏹️ 停止订单执行器..."
    while read pid; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            echo "   已停止 PID: $pid"
        else
            echo "   PID $pid 已停止"
        fi
    done < "$LOG_DIR/order_executor.pid"
    rm "$LOG_DIR/order_executor.pid"
else
    echo "⚠️ 未找到order_executor.pid文件"
fi

# 额外确保所有相关进程都停止
echo ""
echo "🔍 检查是否有残留进程..."
pkill -f "signal_generator.py" 2>/dev/null && echo "   清理 signal_generator.py 进程" || true
pkill -f "order_executor.py" 2>/dev/null && echo "   清理 order_executor.py 进程" || true

echo ""
echo "✅ 交易系统已停止"
echo ""
echo "📊 查看队列状态："
echo "   redis-cli ZCARD trading:signals"
echo ""
echo "🗑️ 清空队列（危险操作）："
echo "   redis-cli DEL trading:signals trading:signals:processing trading:signals:failed"
