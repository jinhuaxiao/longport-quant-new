#!/bin/bash
# 诊断文件描述符泄漏

PID=$(pgrep -f "advanced_technical_trading.py" | head -1)

if [ -z "$PID" ]; then
    echo "❌ 交易脚本没有运行"
    exit 1
fi

echo "=========================================="
echo "文件描述符泄漏诊断"
echo "=========================================="
echo "PID: $PID"
echo ""

# 1. 总体统计
echo "1. 打开的文件描述符统计:"
FD_COUNT=$(ls -la /proc/$PID/fd 2>/dev/null | wc -l)
echo "   总数: $FD_COUNT"
echo ""

# 2. 按类型分类
echo "2. 文件描述符类型分布:"
echo ""

# Socket 连接
SOCKET_COUNT=$(ls -l /proc/$PID/fd 2>/dev/null | grep socket | wc -l)
echo "   Socket 连接: $SOCKET_COUNT"

# 管道
PIPE_COUNT=$(ls -l /proc/$PID/fd 2>/dev/null | grep pipe | wc -l)
echo "   管道 (pipe): $PIPE_COUNT"

# 普通文件
FILE_COUNT=$(ls -l /proc/$PID/fd 2>/dev/null | grep -v socket | grep -v pipe | grep -v "^l" | wc -l)
echo "   普通文件: $FILE_COUNT"

echo ""

# 3. Socket 连接详情
if [ $SOCKET_COUNT -gt 0 ]; then
    echo "3. Socket 连接详情:"
    echo ""

    # TCP 连接
    TCP_COUNT=$(lsof -p $PID 2>/dev/null | grep TCP | wc -l)
    echo "   TCP 连接: $TCP_COUNT"

    # 显示 TCP 连接状态
    echo ""
    echo "   TCP 连接状态分布:"
    lsof -p $PID 2>/dev/null | grep TCP | awk '{print $8}' | sort | uniq -c | while read count state; do
        echo "     $state: $count"
    done

    echo ""
    echo "   TCP 连接目标:"
    lsof -p $PID 2>/dev/null | grep TCP | awk '{print $9}' | cut -d'-' -f2 | sort | uniq -c | sort -rn | head -10 | while read count target; do
        echo "     $target: $count"
    done
fi

echo ""

# 4. 检查是否有泄漏迹象
echo "4. 泄漏检查:"
echo ""

if [ $FD_COUNT -gt 500 ]; then
    echo "   ⚠️ 警告: 文件描述符数量较多 ($FD_COUNT)"
fi

if [ $SOCKET_COUNT -gt 100 ]; then
    echo "   ⚠️ 警告: Socket 连接数量较多 ($SOCKET_COUNT)"
fi

# 检查 ESTABLISHED 连接
ESTABLISHED=$(lsof -p $PID 2>/dev/null | grep ESTABLISHED | wc -l)
if [ $ESTABLISHED -gt 50 ]; then
    echo "   ⚠️ 警告: 活跃 TCP 连接较多 ($ESTABLISHED)"
fi

# 检查 CLOSE_WAIT 连接（泄漏的典型标志）
CLOSE_WAIT=$(lsof -p $PID 2>/dev/null | grep CLOSE_WAIT | wc -l)
if [ $CLOSE_WAIT -gt 0 ]; then
    echo "   ❌ 发现 CLOSE_WAIT 连接: $CLOSE_WAIT (可能存在泄漏)"
fi

echo ""
echo "=========================================="
echo "建议："
echo "=========================================="

if [ $FD_COUNT -gt 500 ] || [ $SOCKET_COUNT -gt 100 ] || [ $CLOSE_WAIT -gt 0 ]; then
    echo "检测到可能的资源泄漏！"
    echo ""
    echo "1. 重启交易脚本:"
    echo "   pkill -f advanced_technical_trading.py"
    echo "   python3 scripts/advanced_technical_trading.py --builtin"
    echo ""
    echo "2. 使用监控脚本持续观察:"
    echo "   python3 scripts/monitor_resources.py"
    echo ""
    echo "3. 如果问题持续，检查日志中的错误"
else
    echo "✅ 资源使用正常"
fi

echo ""
