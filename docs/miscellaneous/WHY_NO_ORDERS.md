# 为什么没有看到下单和Slack通知？

## 🔍 诊断结果

根据日志和系统状态分析，发现以下问题：

### ❌ 主要原因：Order Executor没有运行

```bash
# 检查运行进程
$ ps aux | grep order_executor
# 结果：没有找到order_executor进程！
```

**这是没有看到下单的根本原因**！

### 📊 当前状态

| 组件 | 状态 | 说明 |
|-----|------|-----|
| Signal Generator | ❌ 未运行 | 队列中的32个信号是之前运行时留下的 |
| Order Executor | ❌ 未运行 | **关键问题**：没有消费队列中的信号 |
| Redis Queue | ✅ 正常 | 已清空（之前有32个待处理信号）|
| Slack配置 | ✅ 已配置 | `.env`中有webhook URL |

### 🚧 次要原因：可能的风控拦截

即使Order Executor运行了，以下情况会导致订单被跳过（不下单）：

#### 1️⃣ WEAK_BUY信号过滤 (代码第198行)
```python
if signal_type == "WEAK_BUY" and score < 35:
    logger.debug(f"  ⏭️ 跳过弱买入信号 (评分: {score})")
    return  # 不下单
```

**影响**：
- 30-34分的WEAK_BUY信号会被跳过
- 35分以上的WEAK_BUY会执行
- BUY和STRONG_BUY不受影响

#### 2️⃣ 资金不足 (代码第240行)
```python
if required_cash > available_cash:
    logger.warning(f"  ⚠️ {symbol}: 资金不足")
    return  # 不下单
```

**示例**：
- 需要：$116,400（买400股×$291）
- 可用：$50,000
- 结果：跳过此信号

#### 3️⃣ 预算不足买1手 (代码第228行)
```python
if quantity <= 0:
    logger.warning(f"  ⚠️ {symbol}: 动态预算不足以购买1手")
    return  # 不下单
```

**示例**：
- 9992.HK手数：100股/手
- 当前价：$291
- 1手需要：$29,100
- 动态预算：$2,500（评分40分，5%仓位）
- 结果：跳过此信号

#### 4️⃣ 资金异常 (代码第206-215行)
```python
if available_cash < 0:
    logger.error(f"  ❌ {symbol}: 资金异常（显示为负数）")
    if buy_power < 1000:
        return  # 不下单
```

## 📋 完整执行流程图

```
📥 收到信号: 9992.HK, 类型=WEAK_BUY, 评分=40
     ↓
🔍 开始处理 9992.HK 的 WEAK_BUY 信号
     ↓
1️⃣ 获取账户信息
     ✅ 成功
     ↓
2️⃣ 检查：WEAK_BUY且评分<35？
     ❌ 否（40 >= 35）→ 继续
     ↓
3️⃣ 检查：资金异常？
     ✅ 正常 → 继续
     ↓
4️⃣ 计算动态预算
     • 评分40 → 6.67%仓位
     • 净资产$50,000 → 预算$3,335
     ↓
5️⃣ 获取手数：100股/手
     ↓
6️⃣ 计算购买数量
     • 预算$3,335 ÷ 价格$291 = 11股
     • 向下取整到整手：0手
     ⚠️ 数量 <= 0
     ↓
❌ 跳过：动态预算不足以购买1手
```

## 🎯 不同评分信号的处理结果

假设净资产 = $50,000 HKD

| 标的 | 评分 | 类型 | 预算比例 | 预算金额 | 价格 | 1手 | 能买？ | 原因 |
|-----|-----|------|---------|---------|------|-----|-------|------|
| 9992.HK | 59 | BUY | 19.67% | $9,835 | $291 | 100股 | ❌ | 需要$29,100 > $9,835 |
| 1398.HK | 57 | BUY | 18.00% | $9,000 | $6.50 | 500股 | ✅ | 可买2手=1000股 ($6,500) |
| 0857.HK | 53 | BUY | 15.33% | $7,665 | $8.20 | 500股 | ✅ | 可买1手=500股 ($4,100) |
| GOOGL.US | 48 | BUY | 12.00% | $6,000 | $160 | 1股 | ✅ | 可买37股 ($5,920) |
| 3690.HK | 40 | WEAK_BUY | 6.67% | $3,335 | $98.55 | 100股 | ❌ | 需要$9,855 > $3,335 |
| 0700.HK | 35 | WEAK_BUY | 5.00% | $2,500 | $622 | 100股 | ❌ | 需要$62,200 > $2,500 |

**结论**：
- 高价港股（9992, 0700）即使评分高也可能因预算不足无法购买
- 低价港股（1398, 0857）和美股（GOOGL）更容易购买

## ✅ 解决方案

### 方案1：启动Order Executor（首要！）

```bash
# 清空旧队列（可选）
redis-cli DEL trading:signals trading:signals:processing trading:signals:failed

# 启动signal generator生成新信号
python3 scripts/signal_generator.py &

# 等待5秒让它生成一些信号
sleep 5

# 启动order executor处理信号
python3 scripts/order_executor.py &

# 监控队列和日志
python3 scripts/queue_monitor.py
```

### 方案2：调整预算策略（如果资金不足）

编辑 `scripts/order_executor.py` 第60行：

```python
# 选项A：提高单笔交易预算
self.min_position_size_pct = 0.10  # 从5%提高到10%
self.max_position_size_pct = 0.50  # 从30%提高到50%

# 选项B：关闭动态预算，使用固定金额
self.use_adaptive_budget = False  # 将使用固定$10,000预算
```

### 方案3：过滤高价股票

在Signal Generator中添加价格过滤：

```python
# 在generate_signal之前检查
if current_price > 200 and net_assets < 100000:
    logger.debug(f"  ⏭️ 跳过高价股票 {symbol} (价格=${current_price})")
    return None
```

## 📊 监控和验证

### 查看Order Executor日志
```bash
tail -f logs/order_executor_1.log
```

**期望看到的日志：**
```
✅ 订单执行器初始化完成
📥 收到信号: 1398.HK, 类型=BUY, 评分=57
🔍 开始处理 1398.HK 的 BUY 信号
  💰 HKD 可用资金: $50,000.00
  📊 动态预算计算: 评分=57, 预算比例=18.00%, 金额=$9,000.00

✅ 开仓订单已提交: 116303991698937856
   标的: 1398.HK
   数量: 1000股 (2手 × 500股/手)
   下单价: $6.52
   总额: $6,520.00
```

**可能看到的跳过日志：**
```
⚠️ 9992.HK: 动态预算不足以购买1手
   (手数: 100, 需要: $29,100.00, 动态预算: $9,835.00)
```

### 查看Slack通知

如果订单成功提交，Slack会收到通知：

```
🚀 开仓订单已提交

📋 订单ID: 116303991698937856
📊 标的: 1398.HK
💯 信号类型: BUY
⭐ 综合评分: 57/100

💰 交易信息:
   • 数量: 1000股
   • 价格: $6.52
   • 总额: $6,520.00

📊 技术指标:
   • RSI: 42.5
   • MACD差值: +0.123 (金叉 ✅)
   • 成交量比率: 1.8x (放量 📈)

🎯 风控设置:
   • 止损位: $6.20
   • 止盈位: $7.00
```

## 🔧 调试检查清单

- [ ] Redis正在运行：`redis-cli ping` 返回 PONG
- [ ] Signal Generator正在运行：`ps aux | grep signal_generator`
- [ ] Order Executor正在运行：`ps aux | grep order_executor`
- [ ] 队列有信号：`redis-cli ZCARD trading:signals` > 0
- [ ] Slack webhook配置正确：检查`.env`文件
- [ ] 账户有足够资金：至少$10,000 HKD或USD
- [ ] 日志文件权限正常：`ls -la logs/`

---

**创建日期**: 2025-10-16
**状态**: ✅ 诊断完成
**下一步**: 启动Order Executor并监控日志
