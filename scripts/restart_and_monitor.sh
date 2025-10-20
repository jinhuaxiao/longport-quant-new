#!/bin/bash
# 重启交易脚本并显示详细诊断信息

set -e

echo "=========================================="
echo "重启交易系统"
echo "=========================================="

# 1. 停止现有进程
echo ""
echo "1. 停止现有交易进程..."
pkill -f "advanced_technical_trading.py" 2>/dev/null || echo "   (没有运行的进程)"
sleep 2

# 2. 确认停止
if pgrep -f "advanced_technical_trading.py" > /dev/null; then
    echo "   ❌ 进程仍在运行，强制停止..."
    pkill -9 -f "advanced_technical_trading.py"
    sleep 1
fi
echo "   ✅ 已停止"

# 3. 清理日志
echo ""
echo "2. 准备日志文件..."
LOG_FILE="trading_$(date +%Y%m%d_%H%M%S).log"
echo "   日志文件: $LOG_FILE"

# 4. 启动脚本
echo ""
echo "3. 启动交易脚本..."
echo "   命令: python3 scripts/advanced_technical_trading.py --builtin"
echo ""

# 启动并实时显示日志
python3 scripts/advanced_technical_trading.py --builtin 2>&1 | tee "$LOG_FILE" | \
    grep --line-buffered -E "🚀|✅|❌|📥|📌|📤|📨|⏳|信号|处理器|启动|入队|收到|ERROR|Exception|Traceback"

echo ""
echo "=========================================="
echo "脚本已停止"
echo "完整日志保存在: $LOG_FILE"
echo "=========================================="
