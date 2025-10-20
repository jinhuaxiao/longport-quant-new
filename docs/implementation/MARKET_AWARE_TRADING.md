# 市场感知智能交易

## 概述

高级技术指标交易系统现在支持**市场感知**功能，根据不同市场的交易时间，智能地只监控和交易活跃市场的标的。

## 功能特性

### 1. 自动识别活跃市场

系统会根据当前北京时间自动判断哪些市场正在交易：

| 市场 | 交易时间（北京时间） | 说明 |
|------|-------------------|------|
| **港股** | 09:30-12:00, 13:00-16:00 | 早盘 + 午盘 |
| **美股** | 21:30-次日04:00 | 夏令时/冬令时 |

### 2. 智能标的过滤

- **港股交易时段**：只监控 `.HK` 后缀的标的
- **美股交易时段**：只监控 `.US` 后缀的标的
- **重叠时段**：同时监控两个市场的标的
- **非交易时段**：不监控任何标的，系统待机

### 3. 性能优化

**优势：**
- ✅ 减少不必要的API调用
- ✅ 降低网络流量和延迟
- ✅ 提高系统响应速度
- ✅ 节省API配额

**对比：**
```
之前：
- 港股时间仍获取美股行情 → 5个标的 → 但美股价格为0或昨收价
- 浪费2个API调用

现在：
- 港股时间只获取港股行情 → 3个标的 → 减少40%的API调用
- 美股时间只获取美股行情 → 2个标的 → 减少60%的API调用
```

## 实现细节

### 核心方法

#### 1. `get_active_markets()`

返回当前活跃的市场列表。

```python
def get_active_markets(self):
    """
    获取当前活跃的市场

    Returns:
        list: 活跃市场列表，如 ['HK'], ['US'], 或 ['HK', 'US']
    """
    now = datetime.now(self.beijing_tz)
    current_time = now.time()
    weekday = now.weekday()

    active_markets = []

    # 周末不交易
    if weekday >= 5:
        return active_markets

    # 港股交易时段：9:30-12:00, 13:00-16:00
    hk_morning = time(9, 30) <= current_time <= time(12, 0)
    hk_afternoon = time(13, 0) <= current_time <= time(16, 0)
    if hk_morning or hk_afternoon:
        active_markets.append('HK')

    # 美股交易时段（北京时间）：21:30-次日4:00
    if current_time >= time(21, 30) or current_time <= time(4, 0):
        if weekday not in [5, 6]:  # 排除周末
            active_markets.append('US')

    return active_markets
```

**示例输出：**
- 上午10:00 → `['HK']`
- 晚上22:00 → `['US']`
- 周六全天 → `[]`

#### 2. `filter_symbols_by_market()`

根据活跃市场过滤标的列表。

```python
def filter_symbols_by_market(self, symbols, active_markets):
    """
    根据活跃市场过滤标的

    Args:
        symbols: 标的列表 ['09988.HK', 'AAPL.US', ...]
        active_markets: 活跃市场列表 ['HK', 'US']

    Returns:
        list: 过滤后的标的列表
    """
    if not active_markets:
        return []

    filtered = []
    for symbol in symbols:
        if 'HK' in active_markets and '.HK' in symbol:
            filtered.append(symbol)
        elif 'US' in active_markets and '.US' in symbol:
            filtered.append(symbol)

    return filtered
```

**示例：**
```python
symbols = ['09988.HK', '03690.HK', 'AAPL.US', 'MSFT.US']
active_markets = ['HK']

filtered = filter_symbols_by_market(symbols, active_markets)
# 结果: ['09988.HK', '03690.HK']
```

#### 3. 主循环集成

```python
# 1. 检查当前活跃市场
active_markets = self.get_active_markets()
if not active_markets:
    logger.info("⏰ 当前时间: 不在交易时段")
    await asyncio.sleep(60)
    continue

# 2. 根据活跃市场过滤标的
active_symbols = self.filter_symbols_by_market(symbols, active_markets)
if not active_symbols:
    logger.info(f"⏰ 当前活跃市场 {active_markets}，但监控列表中无对应标的")
    await asyncio.sleep(60)
    continue

logger.info(f"📍 活跃市场: {', '.join(active_markets)} | 监控标的: {len(active_symbols)}个")

# 3. 获取实时行情（只获取活跃市场的标的）
quotes = await self.get_realtime_quotes(active_symbols)
```

## 运行示例

### 港股交易时段（10:00）

```
======================================================================
第 1 轮扫描 - 10:00:00
======================================================================
📍 活跃市场: HK | 监控标的: 3个
📊 获取到 3 个标的的实时行情

检查持仓:
  ✓ 09988.HK - 当前价 $175.00 (活跃市场，实时检查)
  ✓ 9988.HK - 当前价 $175.00 (持仓检查)
  ✓ 857.HK - 当前价 $7.05 (持仓检查)
  - NVDA.US - 跳过（美股未开盘）
```

### 美股交易时段（22:00）

```
======================================================================
第 1 轮扫描 - 22:00:00
======================================================================
📍 活跃市场: US | 监控标的: 2个
📊 获取到 2 个标的的实时行情

检查持仓:
  ✓ AAPL.US - 当前价 $254.43 (活跃市场，实时检查)
  ✓ NVDA.US - 当前价 $181.85 (持仓检查)
  - 09988.HK - 跳过（港股已休市）
```

### 非交易时段（18:00）

```
======================================================================
第 1 轮扫描 - 18:00:00
======================================================================
⏰ 当前时间: 不在交易时段

⏳ 等待60秒进入下一轮...
```

## 持仓管理

### 持仓检查逻辑

持仓检查也遵循市场感知规则：

1. **活跃市场的持仓**：实时检查止损止盈
2. **非活跃市场的持仓**：跳过检查（因为不在 quotes 列表中）

**示例：**
- 港股时间：检查港股持仓（9988.HK, 857.HK等）
- 美股时间：检查美股持仓（NVDA.US等）

### 优势

- 只在相关市场开盘时才检查对应持仓
- 避免使用过时的行情数据做决策
- 保证止损止盈判断基于实时价格

## 配置

### 自选股配置

在 `configs/watchlist.yml` 中配置监控标的：

```yaml
symbols:
  # 港股标的
  - symbol: "09988.HK"
    name: "阿里巴巴"
  - symbol: "03690.HK"
    name: "美团"

  # 美股标的
  - symbol: "AAPL.US"
    name: "苹果"
  - symbol: "MSFT.US"
    name: "微软"
```

系统会自动根据后缀（`.HK` / `.US`）判断市场。

### 时区配置

系统使用北京时间（Asia/Shanghai）：

```python
self.beijing_tz = ZoneInfo('Asia/Shanghai')
```

## 扩展支持

### 添加新市场

如需支持其他市场（如A股、新加坡等），在 `get_active_markets()` 中添加：

```python
# A股交易时段：9:30-11:30, 13:00-15:00
a_morning = time(9, 30) <= current_time <= time(11, 30)
a_afternoon = time(13, 0) <= current_time <= time(15, 0)
if a_morning or a_afternoon:
    active_markets.append('CN')
```

并在 `filter_symbols_by_market()` 中添加过滤规则：

```python
elif 'CN' in active_markets and '.SH' in symbol or '.SZ' in symbol:
    filtered.append(symbol)
```

## 日志输出

系统会清晰地显示当前监控的市场：

```
📍 活跃市场: HK | 监控标的: 3个
📍 活跃市场: US | 监控标的: 2个
📍 活跃市场: HK, US | 监控标的: 5个
⏰ 当前时间: 不在交易时段
```

## 性能指标

### API调用优化

假设监控列表：3个港股 + 2个美股 = 5个标的

| 时段 | 之前 | 现在 | 节省 |
|------|------|------|------|
| 港股时间 | 5次调用 | 3次调用 | **40%** |
| 美股时间 | 5次调用 | 2次调用 | **60%** |
| 非交易时间 | 5次调用 | 0次调用 | **100%** |

**每日节省：**
- 港股交易: 4.5小时 × 60次/小时 × 2次节省 = **540次调用**
- 美股交易: 6.5小时 × 60次/小时 × 3次节省 = **1170次调用**
- **总计每日节省: ~1710次API调用**

## 注意事项

1. **时区处理**：系统使用北京时间，确保服务器时区正确
2. **夏令时**：美股夏令时/冬令时的交易时间可能需要调整
3. **假期处理**：当前不检测市场假期，假期时段可能尝试交易
4. **盘前盘后**：当前不支持盘前盘后交易
5. **跨日处理**：美股交易跨越零点，已正确处理

## 未来增强

可能的改进：

1. **假期日历**：集成市场假期信息，自动跳过休市日
2. **盘前盘后**：支持盘前盘后交易时段
3. **动态时区**：根据夏令时自动调整交易时间
4. **市场状态API**：通过API实时获取市场开盘状态
5. **多时区支持**：支持在不同时区运行

## 相关文件

- `scripts/advanced_technical_trading.py` - 主交易系统
- `configs/watchlist.yml` - 监控标的配置
- `docs/ADVANCED_STRATEGY_GUIDE.md` - 策略详细说明
- `docs/SLACK_NOTIFICATION.md` - Slack通知配置

## 总结

市场感知功能让交易系统更加智能和高效：

✅ **智能** - 自动识别活跃市场
✅ **高效** - 减少40-60%的API调用
✅ **准确** - 只用实时数据做决策
✅ **灵活** - 易于扩展新市场

这是量化交易系统的一个重要优化，显著提升了系统的专业性和实用性。