#!/bin/bash
#
# 安全重启交易系统
# 用于修复文件描述符泄漏后的重启
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "======================================================================"
echo "  安全重启交易系统 (修复文件描述符泄漏)"
echo "======================================================================"
echo

# 1. 检查旧进程
echo "🔍 查找旧进程..."
OLD_PID=$(ps aux | grep "advanced_technical_trading.py" | grep -v grep | awk '{print $2}' | head -1)

if [ -n "$OLD_PID" ]; then
    echo "   找到旧进程: PID=$OLD_PID"

    # 检查文件描述符数量
    if [ -d "/proc/$OLD_PID/fd" ]; then
        FD_COUNT=$(ls /proc/$OLD_PID/fd 2>/dev/null | wc -l)
        echo "   旧进程文件描述符数量: $FD_COUNT"

        if [ "$FD_COUNT" -gt 500 ]; then
            echo "   ⚠️  检测到文件描述符泄漏 ($FD_COUNT > 500)"
        fi
    fi

    # 停止旧进程
    echo "   🛑 停止旧进程..."
    kill "$OLD_PID" 2>/dev/null || true
    sleep 2

    # 确保进程已终止
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "   ⚠️  进程未响应，强制终止..."
        kill -9 "$OLD_PID" 2>/dev/null || true
        sleep 1
    fi

    echo "   ✅ 旧进程已停止"
else
    echo "   ✅ 没有运行中的进程"
fi

echo

# 2. 清理僵尸连接（可选）
echo "🔧 检查PostgreSQL连接..."
CLOSE_WAIT_COUNT=$(netstat -antp 2>/dev/null | grep -c CLOSE_WAIT | grep postgres || echo "0")
if [ "$CLOSE_WAIT_COUNT" -gt 0 ]; then
    echo "   ⚠️  发现 $CLOSE_WAIT_COUNT 个 CLOSE_WAIT 连接"
    echo "   提示: 新版本已优化连接池，重启后应自动修复"
else
    echo "   ✅ 无僵尸连接"
fi

echo

# 3. 显示代码版本信息
echo "📋 代码版本检查..."
echo "   stop_manager.py 连接池配置:"
grep -A 2 "max_size=" "$PROJECT_DIR/src/longport_quant/persistence/stop_manager.py" | head -3 || echo "   无法读取配置"

echo

# 4. 启动新进程
echo "🚀 启动新进程..."
echo "   命令: python3 scripts/advanced_technical_trading.py --builtin"
echo

cd "$PROJECT_DIR"

# 选择日志方式
echo "选择日志输出方式:"
echo "  1) 直接输出到终端"
echo "  2) 输出到文件 trading_new.log"
echo "  3) 后台运行（nohup）"
echo
read -p "请选择 (1-3) [默认: 2]: " LOG_CHOICE
LOG_CHOICE=${LOG_CHOICE:-2}

case $LOG_CHOICE in
    1)
        echo "启动中 (直接输出)..."
        python3 scripts/advanced_technical_trading.py --builtin
        ;;
    2)
        echo "启动中 (日志文件: trading_new.log)..."
        python3 scripts/advanced_technical_trading.py --builtin 2>&1 | tee trading_new.log &
        NEW_PID=$!
        echo "   ✅ 已启动，PID=$NEW_PID"
        echo "   查看日志: tail -f trading_new.log"
        ;;
    3)
        echo "启动中 (后台运行)..."
        nohup python3 scripts/advanced_technical_trading.py --builtin > trading_nohup.log 2>&1 &
        NEW_PID=$!
        echo "   ✅ 已启动，PID=$NEW_PID"
        echo "   查看日志: tail -f trading_nohup.log"
        ;;
    *)
        echo "❌ 无效选择"
        exit 1
        ;;
esac

echo

# 5. 验证新进程
if [ "$LOG_CHOICE" != "1" ]; then
    echo "⏳ 等待3秒后验证进程..."
    sleep 3

    if ps -p "$NEW_PID" > /dev/null 2>&1; then
        echo "   ✅ 新进程运行正常 (PID=$NEW_PID)"

        # 显示初始文件描述符数量
        if [ -d "/proc/$NEW_PID/fd" ]; then
            INITIAL_FD=$(ls /proc/$NEW_PID/fd 2>/dev/null | wc -l)
            echo "   📊 初始文件描述符数量: $INITIAL_FD"
        fi

        echo
        echo "✅ 重启完成！"
        echo
        echo "📊 监控建议:"
        echo "   1. 运行资源监控: python3 scripts/monitor_resources.py"
        echo "   2. 每10分钟会自动重置连接池"
        echo "   3. 文件描述符超过500会收到警告"
        echo
    else
        echo "   ❌ 进程启动失败，请检查日志"
        exit 1
    fi
fi

echo "======================================================================"
