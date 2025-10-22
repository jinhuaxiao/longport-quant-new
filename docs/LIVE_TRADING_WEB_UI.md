# 实盘交易Web界面使用指南

本文档说明如何通过Web界面管理和监控实盘交易系统，完全替代命令行脚本操作。

---

## 🎯 功能对比

### ❌ 以前需要命令行操作

```bash
# 启动信号生成器
python scripts/signal_generator.py &

# 启动订单执行器
python scripts/order_executor.py &

# 停止系统
bash scripts/stop_trading_system.sh

# 查看队列状态
redis-cli ZCARD trading:signals

# 清空队列
redis-cli DEL trading:signals
```

### ✅ 现在可以通过Web界面操作

访问 `http://localhost:3000/live/monitor`

所有功能一键完成：
- 🚀 启动/停止系统
- ⏸️ 暂停/恢复交易
- 📊 监控进程状态
- 📈 查看实时持仓
- 🔍 监控Redis队列
- 🗑️ 清理队列数据

---

## 📋 Web界面功能

### 1. 系统控制面板

**功能**：
- **Start System** - 同时启动 signal_generator 和 order_executor
- **Stop System** - 停止所有交易进程
- **Pause Trading** - 暂停交易（停止 order_executor，保留 signal_generator）
- **Resume Trading** - 恢复交易（重启 order_executor）

**进程状态显示**：
- Signal Generator - 信号生成器状态、PID、运行时长
- Order Executor - 订单执行器状态、PID、运行时长

**等同于脚本**：
```bash
# Start System =
python scripts/signal_generator.py &
python scripts/order_executor.py &

# Stop System =
bash scripts/stop_trading_system.sh
```

### 2. Redis队列监控

**显示内容**：
- Pending - 待处理信号数量
- Processing - 处理中信号数量
- Failed - 失败信号数量
- Total Processed - 总处理数量
- Success Rate - 成功率

**操作**：
- **Clear** - 清空指定队列
- **Retry All** - 重试所有失败的信号

**等同于命令**：
```bash
# 查看队列
redis-cli ZCARD trading:signals        # Pending count
redis-cli ZCARD trading:signals:failed # Failed count

# 清空队列
redis-cli DEL trading:signals          # Clear pending
redis-cli DEL trading:signals:failed   # Clear failed
```

### 3. 实时持仓监控

**显示内容**：
- Symbol - 股票代码
- Quantity - 持仓数量
- Entry Price - 入场价格
- Current Price - 当前价格
- Market Value - 市值
- P&L - 盈亏金额和百分比
- Risk Level - 风险等级（Low/Medium/High）
- Holding Period - 持仓时长

**自动刷新**：每5秒更新一次

### 4. 系统指标

实时显示6个关键指标：
1. Active Strategies - 活跃策略数
2. Active Positions - 持仓数量
3. Pending Orders - 待处理订单
4. Today's Trades - 今日交易次数
5. Today's P&L - 今日盈亏
6. Errors - 错误计数

### 5. 系统日志

显示最近的系统活动：
- Signal Generator 分析日志
- Order Executor 订单日志
- 交易成功/失败记录
- 警告和错误信息

---

## 🔧 后端API端点

Web界面调用以下API实现功能：

### 系统控制

```
POST /api/system/control/start     # 启动系统
POST /api/system/control/stop      # 停止系统
POST /api/system/control/pause     # 暂停交易
POST /api/system/control/resume    # 恢复交易
GET  /api/system/process-status    # 进程状态
```

### 队列管理

```
GET  /api/queue/stats               # 队列统计
POST /api/queue/clear/{queue_type}  # 清空队列 (pending/processing/failed)
POST /api/queue/retry-failed        # 重试失败信号
GET  /api/queue/recent?limit=10     # 最近信号
```

### 持仓和订单

```
GET  /api/positions                 # 所有持仓
GET  /api/positions/{symbol}        # 单个持仓
GET  /api/system/status             # 系统状态
```

---

## 🚀 快速开始

### 1. 启动后端API

```bash
cd /home/user/longport-quant-new

# 启动监控API（如果还没启动）
python -m longport_quant.monitoring.api
```

API 默认运行在 `http://localhost:8000`

### 2. 启动前端

```bash
cd frontend

# 安装依赖（首次）
pnpm install

# 启动开发服务器
pnpm dev
```

前端默认运行在 `http://localhost:3000`

### 3. 访问实盘控制台

打开浏览器访问：
```
http://localhost:3000/live/monitor
```

---

## 📊 使用场景

### 场景1：启动交易系统

1. 访问 `/live/monitor`
2. 点击 **Start System** 按钮
3. 查看进程状态变为 "RUNNING"
4. 观察队列开始接收信号

**以前需要**：
```bash
ssh server
cd /path/to/project
python scripts/signal_generator.py &
python scripts/order_executor.py &
```

### 场景2：暂停交易

当市场波动剧烈，需要暂时停止下单：

1. 点击 **Pause Trading** 按钮
2. Order Executor 停止
3. Signal Generator 继续运行（信号继续积累）
4. 稍后点击 **Resume Trading** 恢复

**以前需要**：
```bash
# 手动kill进程
kill <order_executor_pid>

# 重启
python scripts/order_executor.py &
```

### 场景3：清理失败信号

当发现大量失败信号堆积：

1. 查看 Redis Queue Monitor
2. 点击 Failed 队列的 **Clear** 按钮
3. 或点击 **Retry All** 重新处理

**以前需要**：
```bash
redis-cli DEL trading:signals:failed
```

### 场景4：紧急停止

发现异常需要立即停止所有交易：

1. 点击 **Stop System** 按钮
2. 确认弹窗
3. 所有进程立即终止

**以前需要**：
```bash
bash scripts/stop_trading_system.sh
```

---

## ⚠️ 重要注意事项

### 1. 紧急停止警告

点击 "Stop System" 会立即终止所有进程：
- ✅ 正在处理的信号会保留在队列中
- ⚠️ 未取消的挂单可能仍在交易所
- 💡 建议先暂停交易，确认订单状态后再完全停止

### 2. 清空队列警告

清空队列是**不可逆操作**：
- `pending` - 清空待处理信号（慎用！）
- `processing` - 清空处理中信号（危险！）
- `failed` - 清空失败信号（安全）

建议只清空 `failed` 队列。

### 3. 进程管理

- Signal Generator 和 Order Executor 可以独立启停
- 如果只想生成信号不下单，只启动 Signal Generator
- 进程PID保存在 `logs/` 目录

### 4. 权限要求

确保运行Web API的用户有权限：
- 启动Python进程
- 写入 `logs/` 目录
- 访问Redis数据库

---

## 🔍 故障排查

### 问题1：点击Start但进程没启动

**检查**：
```bash
# 查看日志
tail -f logs/signal_generator_*.log
tail -f logs/order_executor_*.log

# 检查Python环境
which python3
python3 --version
```

**解决**：
- 确保 `scripts/signal_generator.py` 可执行
- 检查Python依赖是否安装
- 查看错误日志

### 问题2：队列统计显示错误

**检查**：
```bash
# 测试Redis连接
redis-cli ping

# 查看Redis配置
echo $REDIS_URL
```

**解决**：
- 确保Redis服务运行
- 检查 `.env` 中的 Redis URL配置

### 问题3：持仓数据不刷新

**检查**：
```bash
# 测试API连接
curl http://localhost:8000/api/positions

# 查看浏览器控制台
# 打开 DevTools → Console 查看错误
```

**解决**：
- 确保后端API运行正常
- 检查CORS配置
- 查看网络请求状态

---

## 📈 监控最佳实践

### 1. 日常监控

**推荐流程**：
1. 早上开盘前检查系统状态
2. 启动交易系统
3. 监控队列处理速度
4. 查看实时持仓和P&L
5. 收盘后查看日志和统计

### 2. 风控检查

**定期检查**：
- 持仓集中度（Risk Level列）
- 今日交易次数（避免过度交易）
- 错误计数（Error指标）
- 队列失败率（Success Rate）

### 3. 性能优化

**监控指标**：
- 队列Pending数量（不应持续增长）
- 信号处理延迟
- 进程运行时长（Uptime）

如果Pending持续增长：
- 增加Order Executor实例数
- 优化策略逻辑
- 检查API限流

---

## 🎨 界面特性

### 专业设计

参考 Bloomberg Terminal 和 TradingView：
- 深色主题，低饱和度配色
- Monospace 字体显示数字
- 高信息密度布局
- 实时自动刷新

### 颜色语义

- 🟢 绿色 - 盈利、运行中、成功
- 🔴 红色 - 亏损、错误、危险操作
- 🟡 黄色 - 警告、处理中
- ⚪ 灰色 - 中性、停止

### 快捷键（计划中）

- `Ctrl+S` - 启动系统
- `Ctrl+X` - 停止系统
- `Ctrl+P` - 暂停交易
- `Ctrl+R` - 刷新数据

---

## 🔗 相关文档

- [专业UI设计指南](./architecture/PROFESSIONAL_UI_DESIGN_GUIDE.md)
- [前端快速开始](./FRONTEND_QUICKSTART.md)
- [API文档](./API_DOCUMENTATION.md)
- [系统架构](./architecture/ARCHITECTURE.md)

---

## 💡 未来计划

### 短期（1-2周）

- [ ] WebSocket实时数据推送
- [ ] 实时日志流显示
- [ ] 策略参数在线调整
- [ ] 移动端响应式适配

### 中期（1个月）

- [ ] 历史回测结果查看
- [ ] 策略性能对比分析
- [ ] 自动化告警通知
- [ ] 导出报表功能

### 长期（3个月）

- [ ] 多用户权限管理
- [ ] 策略可视化编辑器
- [ ] AI辅助决策建议
- [ ] 移动App开发

---

**现在你可以完全通过Web界面管理交易系统，不再需要SSH登录服务器！** 🎉
