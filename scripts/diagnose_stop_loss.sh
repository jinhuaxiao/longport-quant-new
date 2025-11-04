#!/bin/bash
echo "=========================================="
echo "止损系统诊断报告"
echo "生成时间: $(date)"
echo "=========================================="
echo ""

echo "1. signal_generator进程状态:"
ps aux | grep -v grep | grep signal_generator || echo "  ❌ 未运行"
echo ""

echo "2. 最新日志时间:"
ls -lth logs/signal_generator*.log 2>/dev/null | head -1 || echo "  ❌ 无日志文件"
echo ""

echo "3. 最近60秒的止损检查:"
tail -1000 logs/signal_generator*.log 2>/dev/null | grep -E "check_exit_signals|触发止损" | tail -5 || echo "  ⚠️ 无止损检查记录"
echo ""

echo "4. 数据库止损记录:"
psql -h 127.0.0.1 -U postgres -d longport_next_new -t -c "
SELECT COUNT(*) || ' 个活跃止损记录' FROM position_stops WHERE status = 'active';
" 2>/dev/null || echo "  ❌ 数据库查询失败"
echo ""

echo "5. Redis队列状态:"
echo "  signals_queue: $(redis-cli -h 127.0.0.1 -p 6379 LLEN signals_queue 2>/dev/null || echo '查询失败')"
echo "  signals_processing: $(redis-cli -h 127.0.0.1 -p 6379 LLEN signals_processing 2>/dev/null || echo '查询失败')"
echo "  signals_failed: $(redis-cli -h 127.0.0.1 -p 6379 LLEN signals_failed 2>/dev/null || echo '查询失败')"
echo ""

echo "6. 当前持仓的止损设置:"
psql -h 127.0.0.1 -U postgres -d longport_next_new -c "
SELECT
    symbol,
    entry_price,
    stop_loss,
    ROUND((stop_loss - entry_price) / entry_price * 100, 2) as stop_pct,
    quantity,
    TO_CHAR(created_at, 'MM-DD HH24:MI') as created
FROM position_stops
WHERE status = 'active'
ORDER BY created_at DESC
LIMIT 10;
" 2>/dev/null || echo "  ❌ 数据库查询失败"

echo ""
echo "=========================================="
echo "诊断完成"
echo "=========================================="
