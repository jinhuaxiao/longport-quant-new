#!/bin/bash
#
# 启动解耦后的交易系统
#
# 组件：
#  1. Signal Generator - 信号生成器（1个实例）
#  2. Order Executor - 订单执行器（可以启动多个实例）
#  3. Queue Monitor - 队列监控（可选）
#

set -e  # 遇到错误立即退出

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           启动解耦交易系统 (Decoupled Trading System)        ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# 检查Redis是否运行
echo "🔍 检查Redis服务..."
if ! redis-cli ping > /dev/null 2>&1; then
    echo "❌ Redis未运行！"
    echo "   请先启动Redis: redis-server"
    echo "   或者使用Docker: docker run -d -p 6379:6379 redis:latest"
    exit 1
fi
echo "✅ Redis运行正常"
echo ""

# 创建日志目录
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"

# 启动信号生成器
echo "🚀 启动信号生成器..."
nohup python3 "$SCRIPT_DIR/signal_generator.py" \
    > "$LOG_DIR/signal_generator.log" 2>&1 &
GENERATOR_PID=$!
echo "   PID: $GENERATOR_PID"
echo "   日志: $LOG_DIR/signal_generator.log"
echo "$GENERATOR_PID" > "$LOG_DIR/signal_generator.pid"
echo ""

# 等待信号生成器启动
sleep 2

# 启动订单执行器（默认1个实例，可以通过参数指定更多）
EXECUTOR_COUNT=${1:-1}
echo "🚀 启动 $EXECUTOR_COUNT 个订单执行器..."
for i in $(seq 1 $EXECUTOR_COUNT); do
    nohup python3 "$SCRIPT_DIR/order_executor.py" \
        > "$LOG_DIR/order_executor_$i.log" 2>&1 &
    EXECUTOR_PID=$!
    echo "   Executor #$i PID: $EXECUTOR_PID"
    echo "   日志: $LOG_DIR/order_executor_$i.log"
    echo "$EXECUTOR_PID" >> "$LOG_DIR/order_executor.pid"
    sleep 1
done
echo ""

# 询问是否启动监控
read -p "是否启动队列监控？(y/n) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "🚀 启动队列监控..."
    python3 "$SCRIPT_DIR/queue_monitor.py"
else
    echo "✅ 交易系统启动完成！"
    echo ""
    echo "📊 查看队列状态："
    echo "   python3 scripts/queue_monitor.py"
    echo ""
    echo "📋 查看日志："
    echo "   tail -f $LOG_DIR/signal_generator.log"
    echo "   tail -f $LOG_DIR/order_executor_1.log"
    echo ""
    echo "⏹️ 停止系统："
    echo "   bash scripts/stop_trading_system.sh"
fi
