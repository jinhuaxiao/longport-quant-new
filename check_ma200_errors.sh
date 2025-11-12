#!/bin/bash

# VIXY MA200错误监控脚本
# 用于检查是否有新的MA200相关错误出现

echo "========================================"
echo "📊 VIXY MA200 错误监控"
echo "========================================"
echo ""

# 检查最近的错误
RECENT_ERRORS=$(grep "ERROR.*MA200\|ERROR.*get_candlesticks" logs/signal_generator_live_001.log 2>/dev/null | tail -5)

if [ -z "$RECENT_ERRORS" ]; then
    echo "✅ 没有发现MA200相关错误"
else
    echo "⚠️ 发现以下错误记录:"
    echo "$RECENT_ERRORS" | while IFS= read -r line; do
        ERROR_TIME=$(echo "$line" | awk '{print $1, $2}')
        echo "  • $ERROR_TIME"
    done

    # 检查是否为新错误（10:39之后）
    NEW_ERRORS=$(echo "$RECENT_ERRORS" | grep "2025-11-08 1[0-9]:" | wc -l)

    if [ $NEW_ERRORS -eq 0 ]; then
        echo ""
        echo "✅ 所有错误都是10:39重启前的历史错误"
        echo "✅ 系统已修复，无新错误产生"
    else
        echo ""
        echo "❌ 发现10:39重启后的新错误！"
        echo "请检查 scripts/signal_generator.py 第827-831行"
    fi
fi

echo ""
echo "📝 代码状态检查:"
CODE_LINE=$(grep -A 4 "self.vixy_symbol" scripts/signal_generator.py | grep "adjust_type")

if [ ! -z "$CODE_LINE" ]; then
    echo "✅ 代码已包含 adjust_type 参数"
    echo "   位置: 第831行 - adjust_type=openapi.AdjustType.NoAdjust"
else
    echo "❌ 代码缺少 adjust_type 参数！"
fi

echo ""
echo "🔄 进程状态:"
ps aux | grep signal_generator | grep -v grep | awk '{print "  PID:", $2, "启动时间:", $9, "运行时长:", $10}' | head -2

echo ""
echo "========================================"