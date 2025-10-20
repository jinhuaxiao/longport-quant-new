# 信号去重和防重复交易优化

**日期**: 2025-10-16
**问题**: 信号生成器每60秒扫描一次，会对同一标的重复生成信号，导致队列堆积和重复下单
**状态**: ✅ 已完成

---

## 🎯 优化目标

1. **防止队列中堆积重复信号** - 如果某标的的信号已在队列中等待，不再生成新信号
2. **防止对已持仓标的重复下单** - 如果已经持有某标的，不再生成买入信号
3. **防止当天重复交易** - 如果今天已经对某标的下过单，不再重复交易

---

## 📊 问题场景示例

### 修改前的问题

```
第1轮扫描 (14:00:00)
  📊 分析 9992.HK (泡泡玛特)
    综合评分: 50/100
    ✅ 生成BUY信号 → 发送到队列

第2轮扫描 (14:01:00) - 60秒后
  📊 分析 9992.HK (泡泡玛特)
    综合评分: 52/100 (评分略有变化)
    ✅ 生成BUY信号 → 再次发送到队列  ❌ 重复！

第3轮扫描 (14:02:00) - 120秒后
  📊 分析 9992.HK (泡泡玛特)
    综合评分: 48/100
    ✅ 生成BUY信号 → 又一次发送到队列  ❌ 再次重复！

结果：
  ❌ 队列中有3个9992.HK的买入信号
  ❌ 如果Order Executor处理慢，可能会对同一标的重复下单
```

### 修改后的行为

```
第1轮扫描 (14:00:00)
  📊 分析 9992.HK (泡泡玛特)
    综合评分: 50/100
    检查去重：
      ✅ 队列中无该标的信号
      ✅ 当前无持仓
      ✅ 今日未交易过
    ✅ 生成BUY信号 → 发送到队列

第2轮扫描 (14:01:00)
  📊 分析 9992.HK (泡泡玛特)
    综合评分: 52/100
    检查去重：
      ❌ 队列中已有该标的的待处理信号
    ⏭️  跳过信号: 队列中已有该标的的待处理信号

第3轮扫描 (14:02:00) - 假设第1轮的信号已被执行，现在已持仓
  📊 分析 9992.HK (泡泡玛特)
    综合评分: 48/100
    检查去重：
      ✅ 队列中无该标的信号
      ❌ 已持有该标的
    ⏭️  跳过信号: 已持有该标的

结果：
  ✅ 队列中只有1个9992.HK的买入信号
  ✅ 不会重复下单
  ✅ 持仓后不会再次买入
```

---

## 🔧 实现的功能

### 1. SignalQueue新增方法

#### `has_pending_signal(symbol, signal_type)`

检查队列中是否已存在该标的的待处理信号

**文件**: `src/longport_quant/messaging/signal_queue.py:381-415`

```python
async def has_pending_signal(self, symbol: str, signal_type: str = None) -> bool:
    """
    检查队列中是否已存在该标的的待处理信号

    Args:
        symbol: 标的代码
        signal_type: 信号类型（可选），如'BUY', 'SELL'

    Returns:
        bool: 是否存在待处理信号
    """
    redis = await self._get_redis()

    # 检查主队列
    main_signals = await redis.zrange(self.queue_key, 0, -1)
    for signal_json in main_signals:
        signal = self._deserialize_signal(signal_json)
        if signal.get('symbol') == symbol:
            if signal_type is None or signal.get('type') == signal_type:
                return True

    # 检查处理中队列
    processing_signals = await redis.zrange(self.processing_key, 0, -1)
    for signal_json in processing_signals:
        signal = self._deserialize_signal(signal_json)
        if signal.get('symbol') == symbol:
            if signal_type is None or signal.get('type') == signal_type:
                return True

    return False
```

**用途**:
- 在生成新信号前检查队列中是否已有该标的的信号
- 避免重复发送

#### `get_pending_symbols()`

获取队列中所有待处理的标的代码集合（用于快速批量去重）

**文件**: `src/longport_quant/messaging/signal_queue.py:417-444`

```python
async def get_pending_symbols(self) -> set:
    """
    获取队列中所有待处理的标的代码（用于快速去重）

    Returns:
        set: 标的代码集合
    """
    redis = await self._get_redis()
    symbols = set()

    # 主队列
    main_signals = await redis.zrange(self.queue_key, 0, -1)
    for signal_json in main_signals:
        signal = self._deserialize_signal(signal_json)
        symbols.add(signal.get('symbol'))

    # 处理中队列
    processing_signals = await redis.zrange(self.processing_key, 0, -1)
    for signal_json in processing_signals:
        signal = self._deserialize_signal(signal_json)
        symbols.add(signal.get('symbol'))

    return symbols
```

**用途**:
- 如果需要批量检查多个标的，可以一次性获取所有待处理标的
- 性能优化（减少多次Redis查询）

---

### 2. SignalGenerator新增功能

#### 今日已交易标的跟踪

**初始化** (`scripts/signal_generator.py:155-157`):
```python
# 今日已交易标的集合（避免重复下单）
self.traded_today = set()
self.current_positions = set()  # 当前持仓标的
```

#### 更新今日已交易标的

**方法**: `_update_traded_today()` (`scripts/signal_generator.py:228-252`)

```python
async def _update_traded_today(self):
    """
    更新今日已交易的标的集合（从数据库查询）
    """
    try:
        await self.stop_manager.connect()
        async with self.stop_manager.pool.acquire() as conn:
            # 查询今天新开的持仓（entry_time是今天的）
            today = datetime.now(self.beijing_tz).date()
            rows = await conn.fetch("""
                SELECT DISTINCT symbol
                FROM position_stops
                WHERE DATE(created_at) = $1
                AND status = 'active'
            """, today)

            self.traded_today = {row['symbol'] for row in rows}

            if self.traded_today:
                logger.info(f"📋 今日已交易标的: {len(self.traded_today)}个")
                logger.debug(f"   详细: {', '.join(sorted(self.traded_today))}")

    except Exception as e:
        logger.warning(f"⚠️ 更新今日已交易标的失败: {e}")
        self.traded_today = set()
```

**查询逻辑**:
- 从`position_stops`表查询今天创建的活跃持仓
- 这些就是今天已经下过单的标的

#### 更新当前持仓标的

**方法**: `_update_current_positions(account)` (`scripts/signal_generator.py:254-271`)

```python
async def _update_current_positions(self, account: Dict):
    """
    更新当前持仓标的集合

    Args:
        account: 账户信息字典
    """
    try:
        positions = account.get("positions", [])
        self.current_positions = {pos["symbol"] for pos in positions if pos.get("quantity", 0) > 0}

        if self.current_positions:
            logger.info(f"💼 当前持仓标的: {len(self.current_positions)}个")
            logger.debug(f"   详细: {', '.join(sorted(self.current_positions))}")

    except Exception as e:
        logger.warning(f"⚠️ 更新当前持仓失败: {e}")
        self.current_positions = set()
```

#### 综合去重检查

**方法**: `_should_generate_signal(symbol, signal_type)` (`scripts/signal_generator.py:273-297`)

```python
async def _should_generate_signal(self, symbol: str, signal_type: str) -> tuple[bool, str]:
    """
    检查是否应该生成信号（去重检查）

    Args:
        symbol: 标的代码
        signal_type: 信号类型（BUY/SELL等）

    Returns:
        (bool, str): (是否应该生成, 跳过原因)
    """
    # 1. 检查队列中是否已有该标的的信号
    if await self.signal_queue.has_pending_signal(symbol, signal_type):
        return False, "队列中已有该标的的待处理信号"

    # 2. 对于买入信号，检查是否已持仓
    if signal_type in ["BUY", "STRONG_BUY", "WEAK_BUY"]:
        if symbol in self.current_positions:
            return False, "已持有该标的"

        # 3. 检查今日是否已交易过
        if symbol in self.traded_today:
            return False, "今日已对该标的下过单"

    return True, ""
```

**检查逻辑**:
1. **队列去重**: 检查队列中是否已有该标的的信号（主队列 + 处理中队列）
2. **持仓去重**: 对于买入信号，检查是否已持有该标的
3. **今日去重**: 对于买入信号，检查今天是否已经下过单

---

### 3. 主循环集成

**位置**: `scripts/signal_generator.py:342-390`

```python
try:
    # 1. 更新今日已交易标的和当前持仓
    await self._update_traded_today()
    try:
        account = await self.trade_client.get_account()
        await self._update_current_positions(account)
    except Exception as e:
        logger.warning(f"⚠️ 获取账户信息失败: {e}")
        account = None

    # 2. 获取实时行情
    symbols = list(all_symbols.keys())
    quotes = await self.quote_client.get_realtime_quote(symbols)

    # 3. 分析每个标的并生成信号
    signals_generated = 0
    for quote in quotes:
        try:
            symbol = quote.symbol
            current_price = float(quote.last_done)

            # ... 检查市场开盘 ...

            # 分析标的并生成信号
            signal = await self.analyze_symbol_and_generate_signal(symbol, quote, current_price)

            if signal:
                # 检查是否应该生成信号（去重检查）
                should_generate, skip_reason = await self._should_generate_signal(
                    signal['symbol'],
                    signal['type']
                )

                if not should_generate:
                    logger.info(f"  ⏭️  跳过信号: {skip_reason}")
                    continue

                # 发送信号到队列
                success = await self.signal_queue.publish_signal(signal)
                # ...
```

**执行流程**:
1. 每轮扫描开始时，先更新今日已交易标的和当前持仓
2. 对每个标的进行技术分析
3. 如果分析结果生成信号，执行去重检查
4. 通过检查后才发送到队列

---

## 📊 效果对比

### 场景1：快速市场波动

**假设**: 某标的评分在30-50之间波动，每分钟都满足生成信号的条件

| 时间 | 修改前 | 修改后 |
|-----|-------|-------|
| 14:00 | ✅ 生成信号1 | ✅ 生成信号1 |
| 14:01 | ✅ 生成信号2 (重复) | ⏭️  跳过 (队列中已有) |
| 14:02 | ✅ 生成信号3 (重复) | ⏭️  跳过 (队列中已有) |
| 14:03 | ✅ 生成信号4 (重复) | ⏭️  跳过 (队列中已有) |
| 14:04 | 信号1被执行，已持仓 | 信号1被执行，已持仓 |
| 14:05 | ✅ 生成信号5 (重复持仓) | ⏭️  跳过 (已持仓) |

**结果**:
- 修改前: 队列中有4个重复信号 + 1个持仓后的重复信号
- 修改后: 只有1个有效信号，后续全部跳过

### 场景2：处理器快速消费

**假设**: Order Executor处理速度很快，信号在30秒内被执行

| 时间 | 修改前 | 修改后 |
|-----|-------|-------|
| 14:00:00 | ✅ 生成信号 | ✅ 生成信号 |
| 14:00:30 | 信号被执行，已持仓 | 信号被执行，已持仓 |
| 14:01:00 | ✅ 生成新信号 (重复持仓) | ⏭️  跳过 (已持仓) |
| 14:02:00 | ✅ 生成新信号 (重复持仓) | ⏭️  跳过 (今日已交易) |

**结果**:
- 修改前: 可能会重复买入同一标的
- 修改后: 一天只交易一次

---

## 📋 日志输出示例

### 每轮扫描开始时

```log
🔄 第 2 轮扫描开始 (2025-10-16 14:01:00)
======================================================================

📋 今日已交易标的: 2个
   详细: 1398.HK, 0857.HK

💼 当前持仓标的: 3个
   详细: 0857.HK, 1398.HK, 9992.HK
```

### 分析标的时的去重检查

```log
📊 分析 9992.HK (泡泡玛特)
  实时行情: 价格=$291.00, 成交量=35,457,643

  信号评分:
    RSI得分: 25/30 (超卖(28.5))
    布林带得分: 25/25 (触及下轨($289.50))
    MACD得分: 20/20 (金叉)
    成交量得分: 10/15 (放量(1.8x))
    趋势得分: 5/10 (中性)

  📈 综合评分: 85/100

  ✅ 决策: 生成买入信号 (得分85 >= 30)
     信号类型: STRONG_BUY
     强度: 0.85
     原因: RSI超卖(28.5), 触及布林带下轨, MACD金叉, 成交量放大(1.8x)

  ⏭️  跳过信号: 已持有该标的
```

或者

```log
📊 分析 3690.HK (美团)
  实时行情: 价格=$98.55, 成交量=12,345,678

  📈 综合评分: 57/100

  ✅ 决策: 生成买入信号 (得分57 >= 30)
     信号类型: BUY

  ⏭️  跳过信号: 队列中已有该标的的待处理信号
```

或者

```log
📊 分析 1398.HK (工商银行)
  实时行情: 价格=$6.52, 成交量=89,234,567

  📈 综合评分: 48/100

  ✅ 决策: 生成买入信号 (得分48 >= 30)
     信号类型: BUY

  ⏭️  跳过信号: 今日已对该标的下过单
```

---

## ⚙️ 配置选项

目前去重功能默认启用，无法关闭（这是正确的行为）。

如果需要允许同一标的当天多次交易，可以注释掉这段代码：

```python
# 在 _should_generate_signal() 中
# 3. 检查今日是否已交易过
if symbol in self.traded_today:
    return False, "今日已对该标的下过单"
```

但**不建议这样做**，因为：
1. 可能导致资金过度集中在单一标的
2. 增加风险暴露
3. 可能触发日内交易限制（某些券商有规定）

---

## 🔧 故障排查

### 问题1：信号全部被跳过

**现象**:
```log
⏭️  跳过信号: 队列中已有该标的的待处理信号
⏭️  跳过信号: 队列中已有该标的的待处理信号
⏭️  跳过信号: 队列中已有该标的的待处理信号
```

**原因**: Order Executor没有运行，信号堆积在队列中

**解决**:
```bash
# 启动Order Executor
python3 scripts/order_executor.py &

# 或清空旧队列重新开始
redis-cli DEL trading:signals trading:signals:processing
```

### 问题2：持仓后仍生成买入信号

**现象**: 已经买入某标的，但下一轮扫描时仍生成买入信号

**原因**:
1. 订单刚提交，还未成交，持仓列表中还没有
2. `get_account()` 调用失败

**检查**:
```bash
# 查看日志中是否有账户信息更新
grep "当前持仓标的" logs/signal_generator.log

# 如果看到 "⚠️ 获取账户信息失败"，说明API调用有问题
```

### 问题3：今日已交易标的更新失败

**现象**:
```log
⚠️ 更新今日已交易标的失败: ...
```

**原因**: 数据库连接问题或表不存在

**检查**:
```bash
# 检查数据库连接
psql -h 127.0.0.1 -U postgres -d longport_next_new -c "SELECT COUNT(*) FROM position_stops"

# 检查表结构
psql -h 127.0.0.1 -U postgres -d longport_next_new -c "\d position_stops"
```

---

## 📊 性能影响

### Redis查询开销

每次信号生成前会调用 `has_pending_signal()`：
- 查询主队列: `ZRANGE trading:signals 0 -1`
- 查询处理中队列: `ZRANGE trading:signals:processing 0 -1`

**预期队列大小**: < 50个信号
**查询延迟**: < 10ms
**对整体性能影响**: 可忽略

### 数据库查询开销

每轮扫描开始时查询一次今日已交易标的：
```sql
SELECT DISTINCT symbol
FROM position_stops
WHERE DATE(created_at) = $1
AND status = 'active'
```

**预期返回行数**: < 20个标的
**查询延迟**: < 50ms
**频率**: 每60秒一次
**对整体性能影响**: 可忽略

---

## ✅ 测试验证

### 测试1：队列去重

```bash
# 启动signal generator但不启动executor
python3 scripts/signal_generator.py &

# 等待2轮扫描（2分钟）
sleep 120

# 检查队列
python3 scripts/queue_monitor.py

# 预期：每个标的最多只有1个待处理信号
```

### 测试2：持仓去重

```bash
# 手动买入一个标的（如1398.HK）
# ...

# 启动signal generator
python3 scripts/signal_generator.py &

# 查看日志
tail -f logs/signal_generator.log | grep "1398.HK"

# 预期：看到 "⏭️  跳过信号: 已持有该标的"
```

### 测试3：今日交易去重

```bash
# 完整运行一次交易系统，下单成功
bash scripts/start_trading_system.sh 1

# 等待订单成交
sleep 60

# 查看position_stops表
psql -h 127.0.0.1 -U postgres -d longport_next_new -c \
  "SELECT symbol, created_at, status FROM position_stops WHERE DATE(created_at) = CURRENT_DATE"

# 预期：看到今天的持仓记录

# 继续运行signal generator
# 预期：对于已交易的标的，看到 "⏭️  跳过信号: 今日已对该标的下过单"
```

---

## 📄 相关文件

| 文件 | 行号 | 修改内容 |
|-----|-----|---------|
| `src/longport_quant/messaging/signal_queue.py` | 381-444 | 添加去重检查方法 |
| `scripts/signal_generator.py` | 155-157 | 添加today/positions集合 |
| `scripts/signal_generator.py` | 228-252 | 添加更新today方法 |
| `scripts/signal_generator.py` | 254-271 | 添加更新positions方法 |
| `scripts/signal_generator.py` | 273-297 | 添加综合去重检查 |
| `scripts/signal_generator.py` | 343-350 | 主循环集成去重更新 |
| `scripts/signal_generator.py` | 382-390 | 信号生成前去重检查 |

---

## 🎯 总结

### 解决的问题

1. ✅ **队列堆积**: 同一标的不会重复进入队列
2. ✅ **重复下单**: 已持仓的标的不会再次生成买入信号
3. ✅ **日内重复交易**: 每天对同一标的只交易一次

### 带来的好处

1. **降低风险**: 避免资金过度集中在单一标的
2. **提高效率**: 减少无效的信号生成和队列处理
3. **节省成本**: 减少重复交易的手续费
4. **简化监控**: 队列中的信号更加清晰，易于管理

### 注意事项

1. 去重检查会增加少量Redis和数据库查询（< 100ms/轮）
2. 如果Order Executor没有运行，所有信号会被跳过（符合预期）
3. 持仓信息依赖于`get_account()` API调用的正确性

---

**状态**: ✅ 已完成并可立即使用
**建议**: 重启Signal Generator以应用这些优化
