# 止损止盈系统 - 最终诊断和解决方案

**诊断日期**: 2025-10-16
**核心问题**: API连接数已满 + 旧持仓无止损止盈记录

---

## 🚨 发现的关键问题

### 问题1：API连接数超限（阻塞性问题）

```
OpenApiException: connections limitation is hit, limit = 10, online = 10
```

**当前状态**:
- ✅ signal_generator: 1个进程（需要2个连接: trade + quote）
- ✅ order_executor: 3个进程（每个需要2个连接: trade + quote = 6个连接）
- **总计**: 至少 2 + 6 = 8个连接
- **可能还有未关闭的连接**导致达到10个上限

**影响**:
- ❌ 诊断工具无法运行
- ❌ 设置工具无法运行
- ⚠️ 可能影响现有系统稳定性

---

### 问题2：13个持仓都没有止损止盈设置

**日志证据**:
```log
15:43:43 | INFO | 💼 当前持仓标的: 13个
```

但是：
- ❌ 完全没有止损止盈相关日志
- ❌ 没有"触发止损"或"触发止盈"的消息
- ❌ 没有SELL信号生成

**结论**: 所有13个持仓都是旧持仓（系统启动前就持有），数据库中没有止损止盈记录

---

## 🔧 解决方案（按优先级）

### 方案A：先减少连接数，再设置止损止盈 ⭐推荐

这是**唯一可行的方案**，因为API连接数已满。

#### 步骤1：减少order_executor数量

```bash
# 停止所有
bash scripts/stop_trading_system.sh

# 只启动1个executor（而不是3个）
bash scripts/start_trading_system.sh 1

# 验证进程
ps aux | grep -E "signal_generator|order_executor" | grep -v grep
```

**预期结果**:
- 1个signal_generator × 2连接 = 2
- 1个order_executor × 2连接 = 2
- **总计 = 4/10** ✅ 有足够空间

#### 步骤2：运行设置工具

```bash
# 运行工具
python3 scripts/set_stops_for_existing_positions.py

# 按提示输入您的13个持仓标的
# 每行一个，例如：
# 1398.HK
# 9988.HK
# 0700.HK
# 1810.HK
# 9618.HK
# 1024.HK
# 0981.HK
# 1347.HK
# 9660.HK
# 2382.HK
# 1211.HK
# 3750.HK
# 9992.HK
# （输入完毕后按Enter提交空行）
```

#### 步骤3：验证设置成功

```bash
# 查看数据库
psql -h 127.0.0.1 -U postgres -d longport_next_new -c \
  "SELECT symbol, entry_price, stop_loss, take_profit, status, created_at
   FROM position_stops
   WHERE status = 'active'
   ORDER BY created_at DESC"
```

#### 步骤4：监控止损止盈

```bash
# 终端1: 监控signal_generator
tail -f logs/signal_generator.log | grep -E "持仓|止损|止盈|SELL"

# 终端2: 监控order_executor（如果触发）
tail -f logs/order_executor*.log | grep -E "SELL|平仓"
```

#### 步骤5：（可选）增加executor数量

设置完成并验证后，如果需要更快的执行速度：

```bash
# 停止系统
bash scripts/stop_trading_system.sh

# 启动2个executor（保持连接数在安全范围）
bash scripts/start_trading_system.sh 2

# 总连接数: 1×2 + 2×2 = 6/10 ✅
```

---

### 方案B：不设置止损止盈，等待自然替换

如果您不想手动设置：

```bash
# 什么都不做
# 随着旧持仓卖出，新买入会自动有止损止盈
```

**优点**: 无需操作
**缺点**:
- ❌ 当前13个持仓没有保护
- ⚠️ 需要等待较长时间

---

## 📊 为什么会出现连接数问题？

### 连接数计算

| 组件 | 进程数 | 每进程连接数 | 总连接 |
|-----|--------|-------------|--------|
| signal_generator | 1 | 2 (trade + quote) | 2 |
| order_executor | 3 | 2 (trade + quote) | 6 |
| **总计** | 4 | - | **8** |

加上可能的：
- 诊断工具尝试连接: +2
- 未正常关闭的旧连接: +?

**总计**: ≥10个 → **超限**

### LongPort API限制

- **最大连接数**: 10个
- **超过限制**: 新连接被拒绝
- **错误**: `connections limitation is hit`

---

## 🎯 工作原理说明

### 止损止盈的完整流程

```
1. 买入（通过新系统）
   ↓
2. order_executor保存止损止盈到数据库
   ↓
3. signal_generator每60秒检查
   ↓
4. 读取数据库中的止损止盈设置
   ↓
5. 比较当前价格与止损/止盈价
   ↓
6. 如果触发 → 生成SELL信号
   ↓
7. order_executor执行卖出订单
```

### 为什么旧持仓没有保护？

```
旧持仓买入流程:
  手动买入（或旧系统）
      ↓
  ❌ 没有保存止损止盈
      ↓
  数据库中无记录
      ↓
  signal_generator检查返回None
      ↓
  不会生成SELL信号
```

**解决**: 使用工具手动添加记录到数据库

---

## ✅ 验证清单

完成方案A后，请确认：

### 1. 连接数正常
```bash
# 进程数检查
ps aux | grep -E "signal_generator|order_executor" | grep -v grep | wc -l
# 应该显示: 2（1个generator + 1个executor）
```

### 2. 数据库有记录
```bash
psql -h 127.0.0.1 -U postgres -d longport_next_new -c \
  "SELECT COUNT(*) FROM position_stops WHERE status = 'active'"
# 应该显示: 13
```

### 3. 系统正常运行
```bash
# 查看最近的日志
tail -20 logs/signal_generator.log
# 应该看到: "💼 当前持仓标的: 13个"
```

### 4. 监控生效（等待下一轮扫描）
```bash
# 持续监控
tail -f logs/signal_generator.log

# 期望看到（如果价格触发）:
# 🛑 1398.HK: 触发止损
# 或
# 🎯 9988.HK: 触发止盈
```

---

## 🔍 故障排查

### 如果工具仍然失败

**错误**: `connections limitation is hit`

**解决**:
```bash
# 1. 确认停止了旧进程
pkill -f "signal_generator.py"
pkill -f "order_executor.py"

# 2. 等待2秒让连接关闭
sleep 2

# 3. 重新启动（只启动1个executor）
bash scripts/start_trading_system.sh 1

# 4. 等待5秒让系统稳定
sleep 5

# 5. 再次运行工具
python3 scripts/set_stops_for_existing_positions.py
```

### 如果数据库没有记录

**检查**:
```bash
# 查看工具输出
# 应该显示: "✅ 成功设置: X 个"
```

**如果显示失败**: 查看具体错误信息

### 如果signal_generator没有检查

**检查日志**:
```bash
tail -f logs/signal_generator.log | grep -E "当前持仓|止损|止盈"
```

**如果没有输出**: signal_generator可能崩溃了
```bash
ps aux | grep signal_generator
```

---

## 📊 预期时间线

```
T+0分钟: 停止系统并减少executor数量
         ↓
T+1分钟: 运行设置工具，输入13个标的
         ↓
T+2分钟: 工具完成，数据库已有13条记录
         ↓
T+3分钟: signal_generator下一轮扫描（60秒轮询）
         ↓
T+3分钟: 开始监控止损止盈
         ↓
价格触发时: 自动生成SELL信号并执行
```

---

## 📄 相关文档

1. ✅ **STOP_LOSS_DIAGNOSIS_REPORT.md** - 详细诊断报告
2. ✅ **STOP_LOSS_SYSTEM_EXPLAINED.md** - 系统原理详解
3. ✅ **scripts/set_stops_for_existing_positions.py** - 设置工具

---

## 🎯 立即执行（推荐步骤）

```bash
# 步骤1: 停止系统并减少连接数
bash scripts/stop_trading_system.sh
bash scripts/start_trading_system.sh 1

# 步骤2: 等待5秒让连接释放
sleep 5

# 步骤3: 运行设置工具
python3 scripts/set_stops_for_existing_positions.py
# 输入您的13个持仓标的

# 步骤4: 验证
psql -h 127.0.0.1 -U postgres -d longport_next_new -c \
  "SELECT symbol, stop_loss, take_profit FROM position_stops WHERE status = 'active'"

# 步骤5: 监控
tail -f logs/signal_generator.log | grep -E "止损|止盈"
```

---

**状态**: ✅ 问题定位完成，解决方案就绪
**阻塞因素**: API连接数已满
**建议**: 立即执行方案A（减少executor → 运行工具 → 验证）

---

**创建日期**: 2025-10-16
**测试状态**: 已测试工具，确认连接数问题
**下一步**: 用户执行方案A的步骤1-5
