# Redis队列清理指南

**问题**: 处理中信号堆积（573个）
**原因**: Order Executor处理缓慢或执行失败
**状态**: 需要清理

---

## 🚨 为什么会堆积？

处理中信号堆积通常由以下原因造成：

1. **Order Executor崩溃** - 信号被移到processing队列后，executor崩溃未能标记完成
2. **执行失败未处理** - 订单执行失败但未调用`mark_signal_failed()`
3. **异常退出** - Ctrl+C强制退出导致信号未清理
4. **长时间未运行executor** - Signal Generator持续生成信号但无人消费

---

## 🔧 快速清理方案

### 方案1：使用清理工具（推荐）

```bash
# 运行交互式清理工具
python3 scripts/cleanup_queues.py
```

**操作步骤**:
1. 查看当前状态（自动显示）
2. 输入 `2` - 清理处理中队列
3. 输入 `y` 确认
4. 完成！

**优点**: 安全、交互式、可以查看详情

---

### 方案2：直接使用Redis命令（最快）

```bash
# 查看当前状态
redis-cli ZCARD trading:signals:processing

# 删除处理中队列（573个信号会被删除）
redis-cli DEL trading:signals:processing

# 验证
redis-cli ZCARD trading:signals:processing
# 应返回: (integer) 0
```

**优点**: 最快
**缺点**: 不可恢复

---

### 方案3：移回主队列（恢复模式）

如果这些信号仍然有效，可以将它们移回主队列重新处理：

```bash
# 运行清理工具
python3 scripts/cleanup_queues.py

# 选择操作 6 - 将处理中信号移回主队列
# 这会将573个信号重新加入队列，但优先级会降低20分
```

**优点**: 信号不会丢失
**缺点**: 如果信号已过期（价格变化），可能不再有效

---

## 📋 清理工具功能说明

### 主菜单

```
1. 查看队列状态                 - 查看各队列的信号数量
2. 清理处理中队列（删除）        - 删除所有processing信号
3. 清理失败队列（删除）          - 删除所有failed信号
4. 清理主队列（删除）            - 删除所有待处理信号
5. 清理所有队列（危险！）        - 清空整个队列系统
6. 将处理中信号移回主队列        - 恢复processing信号
7. 查看待处理信号示例            - 显示main队列前10个信号
8. 查看处理中信号示例            - 显示processing队列前10个信号
9. 查看失败信号示例              - 显示failed队列前10个信号
0. 退出
```

### 使用示例

**查看当前状态**:
```bash
python3 scripts/cleanup_queues.py
```

输出：
```
======================================================================
📊 当前队列状态
======================================================================
  📥 待处理队列 (main):       12 个信号
  ⚙️  处理中队列 (processing): 573 个信号  ← 问题！
  ❌ 失败队列 (failed):        5 个信号
======================================================================
```

**清理处理中队列**:
```
请选择操作 (0-9): 2

⚠️  确认清理处理中队列？(y/N): y

🔄 正在清理处理中队列...
✅ 已清理 573 个处理中信号

======================================================================
📊 当前队列状态
======================================================================
  📥 待处理队列 (main):       12 个信号
  ⚙️  处理中队列 (processing): 0 个信号  ← 已清理！
  ❌ 失败队列 (failed):        5 个信号
======================================================================
```

---

## 🔍 诊断和预防

### 为什么会堆积573个？

**检查Order Executor是否运行**:
```bash
ps aux | grep order_executor
```

**如果没有运行**:
```bash
# 说明之前executor崩溃或被停止了
# 这就是为什么信号堆积在processing队列

# 启动executor
python3 scripts/order_executor.py &
```

**查看executor日志**:
```bash
tail -f logs/order_executor_*.log
```

**常见错误**:
```log
# 错误1: 数据库连接失败
❌ 获取账户信息失败: could not connect to server

# 错误2: API调用失败
❌ 执行订单失败: API rate limit exceeded

# 错误3: 资金不足
⚠️ 9992.HK: 资金不足 (需要: $29,100, 可用: $10,000)
```

### 预防措施

#### 1. 监控Order Executor状态

创建监控脚本 `scripts/check_executor.sh`:
```bash
#!/bin/bash
if ! pgrep -f "order_executor.py" > /dev/null; then
    echo "⚠️ Order Executor未运行！"
    echo "启动命令: python3 scripts/order_executor.py &"
else
    echo "✅ Order Executor正在运行"
fi
```

#### 2. 定期检查队列状态

```bash
# 添加到crontab，每小时检查一次
# crontab -e
0 * * * * cd /data/web/longport-quant-new && python3 scripts/queue_monitor.py >> logs/queue_monitor.log
```

#### 3. 使用进程管理工具

使用`supervisord`或`systemd`管理进程，自动重启崩溃的executor。

#### 4. 改进Order Executor错误处理

确保所有异常都正确调用`mark_signal_failed()`或`mark_signal_completed()`。

---

## 🛠️ 故障排查

### 问题1：清理后立即又堆积

**症状**: 清理processing队列后，几分钟内又堆积了很多

**原因**: Order Executor正在运行但处理失败

**检查**:
```bash
# 查看executor日志
tail -f logs/order_executor_*.log | grep "❌"

# 常见问题：
# - API调用失败
# - 数据库连接断开
# - 资金不足
```

**解决**: 修复根本问题后再重启

---

### 问题2：main队列和processing队列都很多

**症状**:
```
📥 待处理队列 (main):       150 个信号
⚙️  处理中队列 (processing): 573 个信号
```

**原因**:
- Signal Generator生成速度 > Executor处理速度
- 或者Executor根本没运行

**解决**:
```bash
# 1. 停止signal generator（停止新信号生成）
pkill -f signal_generator.py

# 2. 清理处理中队列
redis-cli DEL trading:signals:processing

# 3. 启动更多executor实例（提高处理速度）
python3 scripts/order_executor.py &
python3 scripts/order_executor.py &
python3 scripts/order_executor.py &

# 4. 监控处理速度
python3 scripts/queue_monitor.py

# 5. 等待main队列清空后，再启动signal generator
python3 scripts/signal_generator.py &
```

---

### 问题3：清理后系统不工作

**症状**: 清理所有队列后，系统不再生成或处理信号

**检查**:
```bash
# 1. 确认Redis正常
redis-cli ping

# 2. 确认signal generator运行
ps aux | grep signal_generator

# 3. 确认order executor运行
ps aux | grep order_executor

# 4. 查看日志
tail -f logs/signal_generator.log
tail -f logs/order_executor_*.log
```

**解决**: 重启整个系统
```bash
bash scripts/stop_trading_system.sh
bash scripts/start_trading_system.sh 3
```

---

## 📊 推荐的清理策略

### 场景1：Order Executor长时间未运行

**状况**: processing队列堆积大量旧信号（超过1小时）

**策略**: **直接删除**
```bash
redis-cli DEL trading:signals:processing
```

**理由**:
- 旧信号的价格已失效
- 重新生成更准确

---

### 场景2：Order Executor刚崩溃（<10分钟）

**状况**: processing队列有少量新信号（<50个）

**策略**: **移回主队列**
```bash
python3 scripts/cleanup_queues.py
# 选择 6 - 将处理中信号移回主队列
```

**理由**:
- 信号仍然有效
- 不浪费分析结果

---

### 场景3：系统运行正常但processing队列慢慢增长

**状况**:
- processing: 持续在10-50之间波动
- 每小时增长5-10个

**策略**: **分析根本原因**
```bash
# 查看处理中信号示例
python3 scripts/cleanup_queues.py
# 选择 8 - 查看处理中信号示例

# 检查这些信号为什么处理失败
tail -f logs/order_executor_*.log | grep "这些标的代码"
```

**可能原因**:
- 特定标的总是失败（如停牌、无权限）
- 网络问题导致API调用超时
- 数据库连接池耗尽

---

## 🚀 执行清理（当前情况）

对于你当前的573个堆积信号，推荐方案：

### 快速清理（5秒）

```bash
# 直接删除处理中队列
redis-cli DEL trading:signals:processing

# 验证
redis-cli ZCARD trading:signals:processing
# 应显示: (integer) 0

# 检查其他队列
redis-cli ZCARD trading:signals
redis-cli ZCARD trading:signals:failed
```

### 完整清理（如果main和failed队列也需要清理）

```bash
# 清理所有队列
redis-cli DEL trading:signals trading:signals:processing trading:signals:failed

# 验证
redis-cli ZCARD trading:signals
redis-cli ZCARD trading:signals:processing
redis-cli ZCARD trading:signals:failed
# 全部应显示: (integer) 0
```

### 清理后重启系统

```bash
# 确保旧进程已停止
pkill -f signal_generator.py
pkill -f order_executor.py

# 等待2秒
sleep 2

# 启动新系统
bash scripts/start_trading_system.sh 3

# 监控状态
python3 scripts/queue_monitor.py
```

---

## 📄 相关文档

- `HOW_SIGNALS_ARE_PROCESSED.md` - 信号处理流程详解
- `SIGNAL_DEDUPLICATION.md` - 信号去重机制
- `WHY_NO_ORDERS.md` - 为什么没有下单的诊断

---

**状态**: 🛠️ 工具已就绪，请选择合适的清理方案
**建议**: 对于573个旧信号，直接删除即可
