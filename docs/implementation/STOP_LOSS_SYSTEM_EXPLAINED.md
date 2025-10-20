# 止损止盈系统详解

**更新日期**: 2025-10-16

---

## 🎯 您的问题

> "目前好像并没有看到持仓的标的有止盈止损的卖出"
> "目前这个信号生成是否有判断当前的持仓情况"

**答案**: **系统确实有完整的止损止盈检查和卖出功能**，但可能由于某些原因没有工作。

---

## ✅ 系统设计（完整流程）

### 流程图

```
买入信号生成 (signal_generator.py)
     ↓
买入信号发送到队列
     ↓
订单执行器消费信号 (order_executor.py)
     ↓
提交买入订单 → 订单成交
     ↓
保存止损止盈到数据库 (stop_manager)
     ↓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
信号生成器每60秒扫描一次
     ↓
调用 check_exit_signals() 检查持仓
     ↓
获取持仓列表 → 获取当前价格
     ↓
从数据库读取止损止盈设置
     ↓
判断: 当前价格 <= 止损价? 或 >= 止盈价?
     ↓ YES
生成SELL信号并发送到队列
     ↓
订单执行器消费SELL信号
     ↓
提交卖出订单 → 订单成交
     ↓
清除止损止盈记录
```

---

## 📝 代码实现位置

### 1. 买入时保存止损止盈

**文件**: `scripts/order_executor.py:281-298`

```python
# 11. 记录止损止盈
self.positions_with_stops[symbol] = {
    "entry_price": current_price,
    "stop_loss": signal.get('stop_loss'),
    "take_profit": signal.get('take_profit'),
    "atr": signal.get('indicators', {}).get('atr'),
}

# 保存到数据库
try:
    await self.stop_manager.set_position_stops(
        account_id=account.get("account_id", ""),
        symbol=symbol,
        stop_loss=signal.get('stop_loss'),
        take_profit=signal.get('take_profit')
    )
except Exception as e:
    logger.warning(f"⚠️ 保存止损止盈失败: {e}")
```

**关键点**:
- ✅ order_executor在买入订单成交后会自动保存止损止盈
- ✅ 止损止盈信息保存到 `position_stops` 表
- ⚠️ 如果保存失败，会有警告日志但不会中断流程

---

### 2. 持仓监控和卖出信号生成

**文件**: `scripts/signal_generator.py:468-484`

```python
# 5. 检查现有持仓的止损止盈（生成平仓信号）
try:
    if account:
        exit_signals = await self.check_exit_signals(quotes, account)
    else:
        exit_signals = []

    for exit_signal in exit_signals:
        success = await self.signal_queue.publish_signal(exit_signal)
        if success:
            signals_generated += 1
            logger.success(
                f"  ✅ 平仓信号已发送: {exit_signal['symbol']}, "
                f"原因={exit_signal.get('reason', 'N/A')}"
            )
except Exception as e:
    logger.warning(f"⚠️ 检查止损止盈失败: {e}")
```

**关键点**:
- ✅ signal_generator每60秒扫描一次
- ✅ 调用 `check_exit_signals()` 检查所有持仓
- ⚠️ 如果 `account` 为 `None`，不会检查持仓
- ⚠️ 如果获取账户信息失败，会跳过检查

---

### 3. check_exit_signals() 详细逻辑

**文件**: `scripts/signal_generator.py:888-955`

```python
async def check_exit_signals(self, quotes, account):
    """检查现有持仓的止损止盈条件（生成平仓信号）"""
    exit_signals = []

    try:
        # 获取持仓
        positions = account.get("positions", [])
        if not positions:
            return exit_signals  # 没有持仓，直接返回

        # 创建行情字典
        quote_dict = {q.symbol: q for q in quotes}

        for position in positions:
            symbol = position["symbol"]
            quantity = position["quantity"]
            cost_price = position["cost_price"]

            if symbol not in quote_dict:
                continue

            quote = quote_dict[symbol]
            current_price = float(quote.last_done)

            # 检查是否有止损止盈设置
            stops = await self.stop_manager.get_position_stops(
                account.get("account_id", ""),
                symbol
            )

            if stops:
                # 检查止损
                if stops.get('stop_loss') and current_price <= stops['stop_loss']:
                    logger.warning(
                        f"🛑 {symbol}: 触发止损 "
                        f"(当前=${current_price:.2f}, 止损=${stops['stop_loss']:.2f})"
                    )
                    exit_signals.append({
                        'symbol': symbol,
                        'type': 'STOP_LOSS',
                        'side': 'SELL',
                        'quantity': quantity,
                        'price': current_price,
                        'reason': f"触发止损 (止损价${stops['stop_loss']:.2f})",
                        'score': 100,  # 高优先级
                        'timestamp': datetime.now(self.beijing_tz).isoformat(),
                        'priority': 100,
                    })

                # 检查止盈
                elif stops.get('take_profit') and current_price >= stops['take_profit']:
                    logger.info(
                        f"🎯 {symbol}: 触发止盈 "
                        f"(当前=${current_price:.2f}, 止盈=${stops['take_profit']:.2f})"
                    )
                    exit_signals.append({
                        'symbol': symbol,
                        'type': 'TAKE_PROFIT',
                        'side': 'SELL',
                        'quantity': quantity,
                        'price': current_price,
                        'reason': f"触发止盈 (止盈价${stops['take_profit']:.2f})",
                        'score': 90,  # 高优先级
                        'timestamp': datetime.now(self.beijing_tz).isoformat(),
                        'priority': 90,
                    })

    except Exception as e:
        logger.error(f"❌ 检查退出信号失败: {e}")

    return exit_signals
```

**关键点**:
- ✅ 遍历所有持仓
- ✅ 获取当前价格
- ✅ 从数据库读取止损止盈设置
- ✅ 判断是否触发止损或止盈
- ✅ 生成SELL信号（side='SELL'）

---

### 4. 卖出订单执行

**文件**: `scripts/order_executor.py:308-332`

```python
async def _execute_sell_order(self, signal: Dict):
    """执行卖出订单（止损/止盈）"""
    symbol = signal['symbol']
    signal_type = signal.get('type', 'SELL')
    quantity = signal.get('quantity', 0)
    current_price = signal.get('price', 0)
    reason = signal.get('reason', '平仓')

    # 获取买卖盘
    bid_price, ask_price = await self._get_bid_ask(symbol)

    # 计算下单价格
    order_price = self._calculate_order_price(
        "SELL",
        current_price,
        bid_price=bid_price,
        ask_price=ask_price,
        symbol=symbol
    )

    # 提交订单
    try:
        order = await self.trade_client.submit_order({
            "symbol": symbol,
            "side": "SELL",
            "quantity": quantity,
            "price": order_price
        })

        logger.success(
            f"\n✅ 平仓订单已提交: {order['order_id']}\n"
            f"   标的: {symbol}\n"
            f"   原因: {reason}\n"
            f"   数量: {quantity}股\n"
            f"   价格: ${order_price:.2f}\n"
            f"   总额: ${order_price * quantity:.2f}"
        )

        # 清除止损止盈记录
        if symbol in self.positions_with_stops:
            del self.positions_with_stops[symbol]

        # 发送Slack通知
        if self.slack:
            await self._send_sell_notification(symbol, signal, order, quantity, order_price)
```

**关键点**:
- ✅ order_executor支持SELL信号
- ✅ 会提交卖出订单
- ✅ 清除止损止盈记录

---

## 🐛 为什么可能没有看到卖出？

### 问题1：旧持仓没有止损止盈设置 ⚠️

**症状**:
- 系统启动前就持有的股票
- 没有在数据库中保存止损止盈设置

**原因**:
- 只有通过新系统买入的股票才会自动保存止损止盈
- 手动买入或旧持仓没有止损止盈记录

**解决方法**:
```bash
# 运行诊断工具
python3 scripts/check_stop_loss_system.py

# 如果确认是旧持仓，有两个选择：
# 1. 手动卖出旧持仓
# 2. 等待系统重新买入（会自动设置止损止盈）
```

---

### 问题2：signal_generator未运行 ⚠️

**症状**:
- 没有定期扫描持仓
- 不会生成SELL信号

**检查**:
```bash
ps aux | grep signal_generator.py
```

**解决方法**:
```bash
# 启动signal_generator
python3 scripts/signal_generator.py &

# 查看日志
tail -f logs/signal_generator*.log
```

---

### 问题3：获取账户信息失败 ⚠️

**症状**:
- 日志中出现 "⚠️ 获取账户信息失败"
- `account` 为 `None`
- `check_exit_signals()` 被跳过

**检查日志**:
```bash
tail -f logs/signal_generator*.log | grep "获取账户信息"
```

**可能原因**:
- API权限不足
- 网络问题
- API quota超限

**解决方法**:
- 检查API配置 (configs/settings.toml)
- 检查网络连接
- 查看LongPort API限制

---

### 问题4：止损止盈没有被保存 ⚠️

**症状**:
- order_executor提交订单成功
- 但数据库中没有止损止盈记录

**检查日志**:
```bash
tail -f logs/order_executor*.log | grep "保存止损止盈"
```

**可能原因**:
- 数据库连接失败
- stop_manager保存失败（已在v1.0修复）

**解决方法**:
```bash
# 检查数据库
psql -h 127.0.0.1 -U postgres -d longport_next_new -c \
  "SELECT symbol, entry_price, stop_loss, take_profit, status, created_at
   FROM position_stops
   WHERE status = 'active'
   ORDER BY created_at DESC LIMIT 10"
```

---

### 问题5：价格未触发止损止盈 ✅

**症状**:
- 有止损止盈设置
- 但当前价格未达到触发条件

**检查**:
```bash
# 运行诊断工具（会显示距离触发的百分比）
python3 scripts/check_stop_loss_system.py
```

**说明**: 这是正常情况，系统工作正常

---

## 🔍 诊断步骤

### 步骤1：运行诊断工具

```bash
python3 scripts/check_stop_loss_system.py
```

**诊断工具会检查**:
1. ✅ 当前持仓列表
2. ✅ 每个持仓是否有止损止盈设置
3. ✅ 数据库中的止损止盈记录
4. ✅ signal_generator是否在运行
5. ✅ order_executor是否在运行
6. ✅ 最近的止损止盈相关日志

---

### 步骤2：查看signal_generator日志

```bash
# 实时查看
tail -f logs/signal_generator*.log

# 搜索止损止盈相关日志
tail -f logs/signal_generator*.log | grep -E "止损|止盈|check_exit|SELL"
```

**期望看到**:
```log
🔄 第 10 轮扫描开始
📋 今日已下单标的: 1个
💼 当前持仓标的: 2个
📊 获取到 32 个标的的实时行情

# 如果有触发，会看到：
🛑 1398.HK: 触发止损 (当前=$5.20, 止损=$5.50)
✅ 平仓信号已发送: 1398.HK, 原因=触发止损
```

---

### 步骤3：查看order_executor日志

```bash
# 实时查看
tail -f logs/order_executor*.log

# 搜索SELL订单
tail -f logs/order_executor*.log | grep "SELL\|平仓"
```

**期望看到**:
```log
📥 收到信号: 1398.HK, 类型=STOP_LOSS, 评分=100
🔍 开始处理 1398.HK 的 STOP_LOSS 信号
✅ 平仓订单已提交: ORDER123456
   标的: 1398.HK
   原因: 触发止损
   数量: 200股
   价格: $5.18
```

---

### 步骤4：检查数据库

```bash
# 查看active状态的止损止盈记录
psql -h 127.0.0.1 -U postgres -d longport_next_new -c \
  "SELECT symbol, entry_price, stop_loss, take_profit, status, created_at
   FROM position_stops
   WHERE status = 'active'
   ORDER BY created_at DESC"

# 查看最近完成的止损止盈记录
psql -h 127.0.0.1 -U postgres -d longport_next_new -c \
  "SELECT symbol, entry_price, stop_loss, take_profit, status, exit_price, exit_reason, updated_at
   FROM position_stops
   WHERE status = 'closed'
   ORDER BY updated_at DESC
   LIMIT 10"
```

---

## ✅ 正常工作的标志

如果系统正常工作，您应该看到：

### 1. 日志中有定期检查

```log
# signal_generator.log
🔄 第 10 轮扫描开始
📋 今日已下单标的: 1个
💼 当前持仓标的: 2个
```

### 2. 买入时保存止损止盈

```log
# order_executor.log
✅ 开仓订单已提交: ORDER123
   止损位: $5.50
   止盈位: $6.50
```

### 3. 数据库有记录

```sql
symbol  | entry_price | stop_loss | take_profit | status
--------|-------------|-----------|-------------|--------
1398.HK |        5.80 |      5.50 |        6.50 | active
```

### 4. 触发时生成SELL信号

```log
# signal_generator.log
🛑 1398.HK: 触发止损 (当前=$5.20, 止损=$5.50)
✅ 平仓信号已发送: 1398.HK

# order_executor.log
📥 收到信号: 1398.HK, 类型=STOP_LOSS
✅ 平仓订单已提交: ORDER456
```

---

## 🔧 快速修复

### 如果signal_generator未运行

```bash
python3 scripts/signal_generator.py &
```

### 如果order_executor未运行

```bash
python3 scripts/order_executor.py &
```

### 如果两者都未运行

```bash
bash scripts/start_trading_system.sh 3
```

---

## 📊 监控命令

### 实时监控止损止盈

```bash
# 终端1: 监控signal_generator
tail -f logs/signal_generator*.log | grep -E "止损|止盈|SELL|平仓"

# 终端2: 监控order_executor
tail -f logs/order_executor*.log | grep -E "SELL|平仓"

# 终端3: 监控队列
watch -n 5 "redis-cli ZCARD trading:signals && redis-cli ZCARD trading:signals:processing"
```

---

## 🎯 总结

### 系统设计

✅ **系统确实有完整的止损止盈功能**:
1. 买入时自动保存止损止盈到数据库
2. signal_generator每60秒检查所有持仓
3. 触发时生成SELL信号并发送到队列
4. order_executor执行卖出订单

### 常见问题

如果没有看到卖出，可能的原因（按可能性排序）：

1. **旧持仓没有设置** (最常见) - 系统启动前的持仓
2. **signal_generator未运行** - 无法检查和生成信号
3. **价格未触发** - 当前价格在止损止盈范围内
4. **账户信息获取失败** - API问题导致无法获取持仓
5. **止损止盈保存失败** - 数据库问题（已在v1.0修复）

### 下一步

```bash
# 1. 运行诊断工具
python3 scripts/check_stop_loss_system.py

# 2. 根据诊断结果修复问题

# 3. 监控日志验证修复
tail -f logs/signal_generator*.log | grep -E "止损|止盈"
```

---

**创建日期**: 2025-10-16
**状态**: ✅ 文档完成
