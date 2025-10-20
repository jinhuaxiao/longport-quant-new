#!/bin/bash
# 重启交易脚本的辅助脚本

echo "正在停止现有的交易脚本..."
pkill -f "advanced_technical_trading.py"
sleep 2

echo "清理旧日志..."
# 备份当前日志
if [ -f "trading_$(date +%Y%m%d).log" ]; then
    mv "trading_$(date +%Y%m%d).log" "trading_$(date +%Y%m%d)_backup_$(date +%H%M%S).log"
fi

echo "启动交易脚本..."
nohup python3 scripts/advanced_technical_trading.py --builtin > "trading_$(date +%Y%m%d).log" 2>&1 &

echo "等待3秒..."
sleep 3

echo "显示最新日志（按Ctrl+C停止）..."
tail -f "trading_$(date +%Y%m%d).log"
