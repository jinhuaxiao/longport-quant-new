#!/bin/bash
# 重启交易脚本并启动资源监控

set -e

echo "=========================================="
echo "重启交易系统并启动监控"
echo "=========================================="
echo ""

# 1. 停止现有进程
echo "1. 停止现有交易进程..."
pkill -f "advanced_technical_trading.py" 2>/dev/null || echo "   (没有运行的进程)"
sleep 2

# 确认停止
if pgrep -f "advanced_technical_trading.py" > /dev/null; then
    echo "   ❌ 进程仍在运行，强制停止..."
    pkill -9 -f "advanced_technical_trading.py"
    sleep 1
fi
echo "   ✅ 已停止"
echo ""

# 2. 启动交易脚本（后台运行）
echo "2. 启动交易脚本..."
LOG_FILE="trading_$(date +%Y%m%d_%H%M%S).log"
nohup python3 scripts/advanced_technical_trading.py --builtin > "$LOG_FILE" 2>&1 &
TRADING_PID=$!
echo "   ✅ 交易脚本已启动 (PID: $TRADING_PID)"
echo "   📄 日志文件: $LOG_FILE"
echo ""

# 3. 等待脚本初始化
echo "3. 等待初始化 (5秒)..."
sleep 5
echo ""

# 4. 检查进程是否正常运行
if ps -p $TRADING_PID > /dev/null; then
    echo "✅ 交易脚本运行正常"
else
    echo "❌ 交易脚本启动失败"
    echo "查看日志: tail -50 $LOG_FILE"
    exit 1
fi
echo ""

# 5. 显示选项
echo "=========================================="
echo "系统已启动，请选择："
echo "=========================================="
echo "1. 查看实时日志"
echo "2. 启动资源监控"
echo "3. 同时查看日志和资源监控（分屏）"
echo "4. 退出"
echo ""
read -p "请选择 (1-4): " choice

case $choice in
    1)
        echo ""
        echo "显示实时日志 (按 Ctrl+C 停止)..."
        tail -f "$LOG_FILE"
        ;;
    2)
        echo ""
        echo "启动资源监控..."
        python3 scripts/monitor_resources.py
        ;;
    3)
        # 检查是否安装了 tmux
        if command -v tmux &> /dev/null; then
            echo ""
            echo "使用 tmux 分屏显示..."
            tmux new-session -d -s trading "tail -f $LOG_FILE"
            tmux split-window -h "python3 scripts/monitor_resources.py"
            tmux attach-session -t trading
        else
            echo ""
            echo "❌ 未安装 tmux，无法分屏"
            echo "安装方法: sudo apt-get install tmux"
            echo ""
            echo "启动资源监控..."
            python3 scripts/monitor_resources.py
        fi
        ;;
    4)
        echo "退出"
        ;;
    *)
        echo "无效选择"
        ;;
esac

echo ""
echo "=========================================="
echo "后台监控命令："
echo "  查看日志: tail -f $LOG_FILE"
echo "  资源监控: python3 scripts/monitor_resources.py"
echo "  停止脚本: pkill -f advanced_technical_trading.py"
echo "=========================================="
