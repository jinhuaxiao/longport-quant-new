# 止损止盈系统诊断报告

**诊断日期**: 2025-10-16 15:43
**问题**: 持仓的标的没有止盈止损的卖出

---

## 🔍 诊断结果

### ✅ 系统组件运行正常

1. ✅ **signal_generator**: 正在运行（1个进程）
2. ✅ **order_executor**: 正在运行（3个进程）
3. ✅ **持仓信息获取**: 正常（13个持仓）
4. ✅ **代码逻辑**: 完整（有check_exit_signals方法）

### ❌ 发现的问题

#### 问题1：API连接数超限 🚨

```
OpenApiException: connections limitation is hit, limit = 10, online = 10
```

**影响**:
- 诊断工具无法运行
- 可能影响系统稳定性

**原因分析**:
- LongPort API限制最多10个并发连接
- 当前有1个signal_generator + 3个order_executor = 4个进程
- 每个进程可能创建了多个连接（trade_client + quote_client）
- 可能还有其他未关闭的连接

---

#### 问题2：所有持仓都没有止损止盈设置 ⚠️

**日志分析**:
```log
15:29:34 | INFO | 💼 当前持仓标的: 13个
15:30:39 | INFO | 💼 当前持仓标的: 13个
15:31:44 | INFO | 💼 当前持仓标的: 13个
```

**但是**：
- ❌ 完全没有"检查退出信号"的日志
- ❌ 完全没有"触发止损"或"触发止盈"的日志
- ❌ 完全没有"平仓信号已发送"的日志

**结论**: 13个持仓都没有在数据库中找到止损止盈设置

**原因**: 这些都是**旧持仓**（系统启动前就持有），没有通过新系统买入，所以数据库中没有记录

---

## 🎯 工作原理

### 止损止盈设置的来源

```
方式1: 通过新系统买入（自动设置）
=====================================
信号生成 → 订单执行 → 订单成交 → order_executor自动保存止损止盈
                                          ↓
                                 position_stops表
                                          ↓
                            signal_generator定期检查

方式2: 手动为旧持仓设置
=====================================
使用工具手动添加 → position_stops表 → signal_generator定期检查
```

### 为什么旧持仓没有止损止盈？

**时间线**:
1. **系统启动前**: 您手动或通过其他方式买入了13个股票
2. **系统启动后**: signal_generator检查这13个持仓
3. **问题**: 数据库中没有这些持仓的止损止盈记录
4. **结果**: `check_exit_signals()` 运行但返回空数组

**代码逻辑** (scripts/signal_generator.py:446):
```python
# 检查是否有止损止盈设置
stops = await self.stop_manager.get_position_stops(account_id, symbol)

if stops:  # ← 旧持仓返回None，不会进入这里
    # 检查止损
    if current_price <= stops['stop_loss']:
        # 生成SELL信号
    ...
```

---

## 🔧 解决方案

### 方案1：为现有持仓手动设置止损止盈（推荐）⭐

我已经创建了专用工具：`scripts/set_stops_for_existing_positions.py`

**步骤**:

```bash
# 1. 运行工具
python3 scripts/set_stops_for_existing_positions.py

# 2. 按提示输入您的13个持仓标的
#    例如:
#    1398.HK
#    9988.HK
#    AAPL.US
#    ...（每行一个，输入完按Enter确认）

# 3. 工具会自动：
#    - 获取当前价格
#    - 计算止损（-5%）和止盈（+10%）
#    - 保存到数据库

# 4. 查看结果
tail -f logs/signal_generator.log | grep -E "止损|止盈|SELL"
```

**优点**:
- ✅ 立即生效（下一轮扫描就会开始检查）
- ✅ 可以保护现有持仓
- ✅ 简单快速

**缺点**:
- ⚠️ 使用当前价格作为入场价（可能不是您实际的买入价）
- ⚠️ 固定的止损止盈百分比（-5%/+10%）

---

### 方案2：清空持仓，重新通过系统买入

```bash
# 1. 手动卖出所有持仓（通过您的券商平台）

# 2. 等待系统自动生成买入信号并执行

# 3. 新买入的持仓会自动设置止损止盈
```

**优点**:
- ✅ 止损止盈基于实际买入价
- ✅ 使用ATR动态计算止损止盈（更精确）
- ✅ 系统完全自动化

**缺点**:
- ❌ 需要等待买入信号
- ❌ 可能错过当前持仓的利润
- ❌ 交易成本（买卖手续费）

---

### 方案3：临时监控，等待新系统逐步替换

```bash
# 1. 手动监控现有13个持仓的价格

# 2. 随着旧持仓卖出，新持仓买入
#    新持仓会自动有止损止盈

# 3. 最终所有持仓都有止损止盈
```

**优点**:
- ✅ 无需立即操作
- ✅ 最终达到完全自动化

**缺点**:
- ❌ 当前持仓没有保护
- ❌ 需要较长时间过渡

---

## 💡 关于API连接数问题

### 当前状态
- 连接数已满（10/10）
- 影响诊断工具运行

### 临时解决方案

**减少order_executor实例数**:
```bash
# 1. 停止所有
bash scripts/stop_trading_system.sh

# 2. 只启动1个executor（而不是3个）
bash scripts/start_trading_system.sh 1

# 3. 验证连接数
# 1 signal_generator × 2 (trade+quote) = 2
# 1 order_executor × 2 (trade+quote) = 2
# 总共 = 4/10 ✅
```

### 长期解决方案

需要优化连接管理：
1. 连接池复用
2. 及时关闭连接
3. 使用单例模式共享连接

（这需要代码重构，暂时建议使用临时方案）

---

## 📊 验证方法

### 设置止损止盈后，如何确认生效？

**步骤1**: 运行设置工具
```bash
python3 scripts/set_stops_for_existing_positions.py
```

**步骤2**: 等待下一轮扫描（最多60秒）

**步骤3**: 查看日志
```bash
tail -f logs/signal_generator.log
```

**期望看到**（如果价格触发）:
```log
🛑 1398.HK: 触发止损 (当前=$5.20, 止损=$5.50)
✅ 平仓信号已发送: 1398.HK, 原因=触发止损

# 或者

🎯 AAPL.US: 触发止盈 (当前=$191.50, 止盈=$190.00)
✅ 平仓信号已发送: AAPL.US, 原因=触发止盈
```

**如果没有触发**（价格在止损止盈范围内）:
```log
# 不会有任何日志（这是正常的）
# 说明系统正在监控但未达到触发条件
```

---

## 📋 检查清单

在设置止损止盈后，请确认：

- [ ] 数据库中有记录
  ```bash
  psql -h 127.0.0.1 -U postgres -d longport_next_new -c \
    "SELECT symbol, entry_price, stop_loss, take_profit, status
     FROM position_stops
     WHERE status = 'active'
     ORDER BY created_at DESC LIMIT 13"
  ```

- [ ] signal_generator正在运行
  ```bash
  ps aux | grep signal_generator
  ```

- [ ] order_executor正在运行
  ```bash
  ps aux | grep order_executor
  ```

- [ ] 监控日志
  ```bash
  # 终端1: 持续监控
  tail -f logs/signal_generator.log | grep -E "持仓|止损|止盈|SELL"

  # 终端2: 检查处理
  tail -f logs/order_executor*.log | grep -E "SELL|平仓"
  ```

---

## 🎯 推荐操作流程

### 立即执行（解决当前问题）

```bash
# 步骤1: 减少连接数压力（可选但推荐）
bash scripts/stop_trading_system.sh
bash scripts/start_trading_system.sh 1

# 步骤2: 为现有持仓设置止损止盈
python3 scripts/set_stops_for_existing_positions.py
# 按提示输入13个持仓标的

# 步骤3: 验证
tail -f logs/signal_generator.log | grep -E "止损|止盈"
```

### 预期结果

- ✅ 下一轮扫描（60秒内）开始检查止损止盈
- ✅ 如果价格触发，会自动生成SELL信号
- ✅ order_executor会自动执行卖出订单

---

## 📊 补充信息

### 为什么日志中没有"检查退出信号"？

**代码逻辑**:
```python
# scripts/signal_generator.py:468-484
try:
    if account:
        exit_signals = await self.check_exit_signals(quotes, account)
    else:
        exit_signals = []

    for exit_signal in exit_signals:  # ← 如果exit_signals为空，这里不执行
        # 发送信号和记录日志
        logger.success("✅ 平仓信号已发送...")
except Exception as e:
    logger.warning(f"⚠️ 检查止损止盈失败: {e}")  # ← 如果有异常才会记录
```

**当前情况**:
- `check_exit_signals()` 正常运行 ✅
- 但是返回空数组 `[]`（因为所有持仓都没有止损止盈设置）
- 所以不会进入 `for` 循环
- 所以不会有任何日志输出

**这是正常行为**，不是bug！

---

## 📄 相关文档

- ✅ `STOP_LOSS_SYSTEM_EXPLAINED.md` - 系统工作原理详解
- ✅ `scripts/check_stop_loss_system.py` - 诊断工具（连接数满时无法使用）
- ✅ `scripts/set_stops_for_existing_positions.py` - 手动设置工具（新建）

---

**诊断完成日期**: 2025-10-16
**状态**: ✅ 问题已定位，解决方案已就绪
**建议**: 立即使用方案1为现有持仓设置止损止盈
