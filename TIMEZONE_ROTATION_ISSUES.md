# 时区轮动工作流程问题分析

**分析时间**: 2025-11-07
**分析范围**: 时区轮动自动卖出信号生成流程

---

## 🔍 发现的问题

### 1. ❌ 美股时间窗口不完整

**位置**: `scripts/signal_generator.py:3258`

**当前代码**:
```python
# 美股收盘前检查（22:00-23:00 北京时间）
if now.hour == 22:
    should_check_us = True
    logger.info("🕐 美股收盘前时段：检查美股持仓轮换机会...")
```

**问题**:
- 只检查了 `hour == 22` (22:00-22:59)
- 文档和注释说的是 22:00-23:00，应该包含 23:00-23:59

**影响**:
- 23:00-23:59 期间不会触发美股轮动检查
- 错过了 1 小时的轮动窗口

**修复方案**:
```python
# 美股收盘前检查（22:00-23:59 北京时间）
if now.hour == 22 or now.hour == 23:
    should_check_us = True
    logger.info("🕐 美股收盘前时段：检查美股持仓轮换机会...")
```

---

### 2. ❌ 轮动信号缺少 `side` 字段

**位置**: `scripts/signal_generator.py:3352-3373`

**当前代码**:
```python
rotation_signal = {
    'symbol': symbol,
    'type': 'ROTATION_SELL',
    'price': current_price,
    'score': 90,
    # ❌ 缺少 'side' 字段
    # ❌ 缺少 'quantity' 字段
    ...
}
```

**对比其他信号** (例如 STOP_LOSS):
```python
signal = {
    'symbol': symbol,
    'type': 'STOP_LOSS',
    'side': 'SELL',        # ✅ 有 side 字段
    'price': current_price,
    'quantity': quantity,   # ✅ 有 quantity 字段
    'reason': f"...",
    'score': 100,
    ...
}
```

**问题**:
- 订单执行器可能无法识别这是卖出订单
- 订单执行器不知道应该卖出多少股

**影响**:
- 🔴 **严重**: 轮动信号可能无法被正确执行
- 订单执行器可能报错或跳过该信号

---

### 3. ❌ 轮动信号缺少 `quantity` 字段

**位置**: `scripts/signal_generator.py:3335-3383`

**当前流程**:
```python
for rot_pos in rotatable_positions:
    symbol = rot_pos.symbol  # ✅ 有 symbol
    # rot_pos 只包含: symbol, market_value, rotation_score, profit_pct, holding_hours, reason
    # ❌ 没有 quantity 信息

    rotation_signal = {
        'symbol': symbol,
        'type': 'ROTATION_SELL',
        'price': current_price,
        # ❌ 没有 'quantity' 字段
        ...
    }
```

**问题根源**:
1. `RotatablePosition` 数据类没有 `quantity` 字段
2. `TimeZoneCapitalManager.identify_rotatable_positions()` 返回的对象中没有 quantity
3. 生成信号时没有从原始持仓中获取 quantity

**修复方案**:

#### 方案 A: 在生成信号时从原始持仓获取 quantity (推荐)

```python
for rot_pos in rotatable_positions:
    symbol = rot_pos.symbol

    # 🔥 从原始持仓中查找 quantity
    position = next((p for p in target_positions if p.get('symbol') == symbol), None)
    if not position:
        logger.warning(f"    {symbol}: 找不到持仓信息，跳过")
        continue

    quantity = position.get('quantity', 0)
    if quantity <= 0:
        logger.warning(f"    {symbol}: 持仓数量无效，跳过")
        continue

    rotation_signal = {
        'symbol': symbol,
        'type': 'ROTATION_SELL',
        'side': 'SELL',           # 🔥 添加 side
        'price': current_price,
        'quantity': quantity,      # 🔥 添加 quantity
        'score': 90,
        ...
    }
```

#### 方案 B: 修改 RotatablePosition 数据类包含 quantity

不推荐，因为需要改动多个文件。

---

### 4. ⚠️ 港股时间窗口逻辑可优化

**位置**: `scripts/signal_generator.py:3248-3254`

**当前代码**:
```python
# 港股收盘前检查（15:30-16:00）
if now.hour == 15 and now.minute >= 30:
    should_check_hk = True
    logger.info("🕐 港股收盘前时段：检查港股持仓轮换机会...")
elif now.hour == 16 and now.minute == 0:
    should_check_hk = True
```

**分析**:
- 15:30-15:59: ✅ 会触发 (hour==15 and minute>=30)
- 16:00: ✅ 会触发 (hour==16 and minute==0)
- 16:01-16:59: ❌ 不会触发（正确，因为已收盘）

**优化建议**:
```python
# 港股收盘前检查（15:30-16:00）
if (now.hour == 15 and now.minute >= 30) or (now.hour == 16 and now.minute == 0):
    should_check_hk = True
    logger.info("🕐 港股收盘前时段：检查港股持仓轮换机会...")
```

---

## 📊 问题汇总

| # | 问题 | 严重程度 | 影响 |
|---|------|----------|------|
| 1 | 美股时间窗口不完整 (22:00-22:59 vs 22:00-23:59) | 🟡 中等 | 错过 1 小时轮动机会 |
| 2 | 轮动信号缺少 `side` 字段 | 🔴 严重 | 订单执行器可能无法识别 |
| 3 | 轮动信号缺少 `quantity` 字段 | 🔴 严重 | 无法执行卖出（不知道卖多少） |
| 4 | 港股时间窗口逻辑可优化 | 🟢 轻微 | 代码可读性 |

---

## 🛠️ 完整修复方案

### 修复位置: `scripts/signal_generator.py`

#### 1. 修复美股时间窗口 (line 3258)

```python
# 修改前
if now.hour == 22:
    should_check_us = True

# 修改后
if now.hour == 22 or now.hour == 23:
    should_check_us = True
```

#### 2. 修复轮动信号缺少字段 (line 3335-3383)

```python
# 在 for rot_pos in rotatable_positions: 循环内添加

for rot_pos in rotatable_positions:
    symbol = rot_pos.symbol

    # 检查今日是否已卖出
    if symbol in self.sold_today:
        logger.debug(f"    {symbol}: 今日已有卖出订单，跳过")
        continue

    # 🔥 从原始持仓获取 quantity
    position = next((p for p in target_positions if p.get('symbol') == symbol), None)
    if not position:
        logger.warning(f"    {symbol}: 找不到持仓信息，跳过")
        continue

    quantity = position.get('quantity', 0)
    if quantity <= 0:
        logger.warning(f"    {symbol}: 持仓数量无效 ({quantity})，跳过")
        continue

    # 获取当前价格
    quote = quote_dict.get(symbol)
    current_price = float(quote.last_done) if quote and quote.last_done else 0

    if current_price <= 0:
        logger.debug(f"    {symbol}: 价格无效，跳过")
        continue

    # 构建卖出信号（添加缺失字段）
    rotation_signal = {
        'symbol': symbol,
        'type': 'ROTATION_SELL',
        'side': 'SELL',           # 🔥 添加
        'price': current_price,
        'quantity': quantity,      # 🔥 添加
        'reason': f"收盘前自动轮换 (评分={rot_pos.rotation_score:.0f}, 原因={rot_pos.reason})",  # 简化
        'score': 90,
        'priority': 90,
        'timestamp': datetime.now(self.beijing_tz).isoformat(),
        'metadata': {
            'rotation_score': rot_pos.rotation_score,
            'profit_pct': rot_pos.profit_pct,
            'market_value': rot_pos.market_value,
            'rotation_reason': rot_pos.reason,
            'auto_rotation': True,
            'target_market': "US" if should_check_hk else "HK"
        }
    }

    rotation_signals.append(rotation_signal)

    logger.success(
        f"    ✅ {symbol}: 生成轮换卖出信号 "
        f"(数量={quantity}, 评分={rot_pos.rotation_score:.0f}, "
        f"盈亏={rot_pos.profit_pct:+.1%}, "
        f"市值=${rot_pos.market_value:,.0f})"
    )
```

---

## ✅ 修复后的工作流程

### 完整流程

```
1. 时间检测
   ├─ 港股: 15:30-16:00 ✅
   └─ 美股: 22:00-23:59 ✅ (修复后)

2. 获取持仓
   ├─ 港股持仓: *.HK ✅
   └─ 美股持仓: *.US ✅

3. 计算轮换评分
   └─ TimeZoneCapitalManager.identify_rotatable_positions() ✅

4. 生成卖出信号
   ├─ 从原始持仓获取 quantity ✅ (修复后)
   ├─ 添加 'side': 'SELL' ✅ (修复后)
   ├─ 添加 'quantity': quantity ✅ (修复后)
   └─ 构建完整信号结构 ✅

5. 发布到 Redis 队列
   └─ signal_queue.publish_signal() ✅

6. 订单执行器处理
   └─ 执行卖出订单 ✅
```

---

## 🧪 验证方法

### 1. 单元测试

创建测试脚本验证信号结构:

```python
# test_rotation_signal_structure.py
import asyncio
from scripts.signal_generator import SignalGenerator

async def test_rotation_signal():
    generator = SignalGenerator()

    # 模拟持仓
    positions = [
        {
            'symbol': '0700.HK',
            'quantity': 100,
            'average_cost': 300.0,
            'market_value': 28000.0
        }
    ]

    # 模拟行情
    quotes = [...]

    # 生成轮动信号
    signals = await generator.check_pre_close_rotation(quotes, account, "RANGE")

    # 验证信号结构
    for signal in signals:
        assert 'symbol' in signal
        assert 'side' in signal and signal['side'] == 'SELL'  # ✅
        assert 'quantity' in signal and signal['quantity'] > 0  # ✅
        assert 'price' in signal
        assert 'type' in signal and signal['type'] == 'ROTATION_SELL'
        print(f"✅ 信号结构验证通过: {signal['symbol']}")

asyncio.run(test_rotation_signal())
```

### 2. 实际运行测试

在 15:45 或 22:30 观察日志输出:

**预期日志** (修复后):
```
🕐 港股收盘前时段：检查港股持仓轮换机会...
  🇭🇰 港股持仓: 3个
  🔍 分析持仓轮换评分...
  🎯 生成自动卖出信号（2个弱势持仓）...
    ✅ 0700.HK: 生成轮换卖出信号 (数量=100, 评分=35, 盈亏=-12.5%, 市值=$28,000)
    ✅ 2318.HK: 生成轮换卖出信号 (数量=200, 评分=28, 盈亏=-18.2%, 市值=$15,000)
  ✅ 轮换信号已发送: 0700.HK, 评分=90
  ✅ 轮换信号已发送: 2318.HK, 评分=90
```

### 3. 订单执行器验证

在 order_executor.py 日志中观察:

**预期日志**:
```
📨 收到轮换卖出信号: 0700.HK
   类型: ROTATION_SELL
   方向: SELL
   数量: 100
   价格: $280.00
   原因: 收盘前自动轮换 (评分=35, 原因=亏损>10% + MACD弱势)
🔄 执行卖出订单...
✅ 订单提交成功: order_id=xxxxx
```

---

## 📝 修复优先级

| 优先级 | 问题 | 理由 |
|--------|------|------|
| P0 (立即修复) | 缺少 `quantity` 字段 | 🔴 阻塞性问题，无法执行订单 |
| P0 (立即修复) | 缺少 `side` 字段 | 🔴 阻塞性问题，可能无法识别 |
| P1 (尽快修复) | 美股时间窗口不完整 | 🟡 功能性问题，错过部分机会 |
| P2 (可选优化) | 港股时间逻辑优化 | 🟢 代码质量，不影响功能 |

---

## 🎯 结论

**当前状态**: 🔴 **时区轮动功能有严重缺陷，需要立即修复**

**关键问题**:
1. ❌ 轮动信号缺少 `quantity` 和 `side` 字段 → 订单无法执行
2. ❌ 美股时间窗口不完整 → 错过 1 小时轮动机会

**修复后状态**: 🟢 **完全可用**

---

**分析完成时间**: 2025-11-07 11:35
**分析人员**: Claude Code
**建议**: 立即修复 P0 问题，然后进行完整测试
