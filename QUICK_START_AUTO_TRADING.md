# 🚀 自动交易快速入门指南

## 核心概念

你理解得完全正确！自动交易系统确实需要分两步：

### 第一步：历史数据同步（一次性初始化）
用于策略回测、技术指标计算等

### 第二步：实时交易（持续运行）
- 实时行情监控
- 策略信号生成
- 账户资金检查
- 持仓情况判断
- 风险控制
- 自动下单

---

## 📦 系统架构总览

```
自动交易系统
├── 数据层
│   ├── 历史K线数据（日线/分钟线）
│   ├── 实时行情数据（轮询/推送）
│   └── 交易日历
│
├── 账户层
│   ├── 资金余额查询
│   ├── 持仓查询
│   └── 可用购买力计算
│
├── 策略层
│   ├── 技术指标计算
│   ├── 交易信号生成
│   └── 多策略协调
│
├── 风控层
│   ├── 资金限制检查
│   ├── 持仓限制检查
│   ├── 单日交易次数限制
│   └── 风险敞口控制
│
└── 执行层
    ├── 订单提交
    ├── 订单状态跟踪
    └── 成交确认
```

---

## 🎯 完整流程演示

### **步骤0：环境准备**

```bash
# 1. 配置长桥API凭证
export LONGPORT_APP_KEY="your_app_key"
export LONGPORT_APP_SECRET="your_app_secret"
export LONGPORT_ACCESS_TOKEN="your_access_token"

# 2. 配置数据库（如果还没有）
export DATABASE_DSN="postgresql+asyncpg://user:password@localhost/quant"
```

---

### **步骤1：初始化历史数据（一次性）**

```bash
# 1. 创建数据库表
python3 scripts/create_database.py

# 2. 创建交易日历表
python3 scripts/create_trading_calendar_table.py

# 3. 同步交易日历数据
python3 scripts/sync_trading_calendar_data.py

# 4. 同步历史K线数据（可选，用于回测）
python3 scripts/sync_historical_klines.py --days 30
```

**预期输出**：
```
✅ 数据库表创建成功
✅ 交易日历同步完成（21条记录）
✅ 历史K线同步完成（30天数据）
```

---

### **步骤2：配置自选股**

编辑 `configs/watchlist_test.yml`：

```yaml
markets:
  hk:
    - 09988.HK  # 阿里巴巴
    - 03690.HK  # 美团
    - 01810.HK  # 小米
  us:
    - AAPL      # 苹果
    - MSFT      # 微软
    - NVDA      # 英伟达
```

---

### **步骤3：运行完整的实时自动交易示例**

#### **模拟模式（推荐先用这个测试）**

```bash
python3 scripts/realtime_auto_trading_example.py
```

**系统会做什么**：
1. ✅ 每60秒获取一次实时行情
2. ✅ 查询账户余额和持仓
3. ✅ 根据策略生成交易信号
4. ✅ 检查资金和持仓限制
5. ✅ **模拟下单**（不实际提交）
6. ✅ 记录交易日志

**示例输出**：
```
============================================================
第 1 轮扫描 - 10:35:23
============================================================
📊 获取到 6 个标的的实时行情
账户余额: {'cash': {'HKD': 100000.0, 'USD': 50000.0}, 'positions': {}, 'position_count': 0}

  ✅ 09988.HK: 生成买入信号 - 28股 @ $175.30
  🔄 [模拟] BUY 09988.HK: 28股 @ $175.30 (总额: $4908.40)

  ✅ AAPL.US: 生成买入信号 - 19股 @ $254.43
  🔄 [模拟] BUY AAPL.US: 19股 @ $254.43 (总额: $4834.17)

🎯 生成 2 个交易信号
⏳ 等待60秒进入下一轮...
```

#### **真实交易模式（确认后才能启动）**

```bash
python3 scripts/realtime_auto_trading_example.py --real

# 系统会提示：
⚠️  警告：即将启动真实交易模式！
⚠️  系统将真实下单，确认请输入 'YES'
> YES

✅ 已确认，启动真实交易模式
```

**真实模式下的输出**：
```
  ✅ [真实] 订单已提交: 123456789 - BUY 09988.HK 28股
  ✅ [真实] 订单已提交: 987654321 - BUY AAPL.US 19股
```

---

## 🔍 实时交易系统的核心逻辑

### **1. 实时行情监控**

```python
# 方式1：轮询模式（当前实现）
async def get_realtime_quotes(symbols):
    quotes = await quote_client.get_realtime_quote(symbols)
    return quotes

# 每60秒执行一次
while True:
    quotes = await get_realtime_quotes(symbols)
    await asyncio.sleep(60)
```

### **2. 账户状态检查**

```python
async def check_account_status():
    # 查询余额
    balances = await trade_client.account_balance()
    cash = {b.currency: float(b.cash) for b in balances}

    # 查询持仓
    positions = await trade_client.stock_positions()

    return {
        "cash": cash,
        "positions": parse_positions(positions),
        "position_count": len(positions)
    }
```

### **3. 策略信号生成**

```python
async def generate_signals(quotes, account):
    signals = []

    for quote in quotes:
        # 检查1：今天已交易过？
        if symbol in executed_today:
            continue

        # 检查2：已达最大持仓数？
        if account["position_count"] >= max_positions:
            continue

        # 检查3：已经持有该标的？
        if symbol in account["positions"]:
            continue

        # 检查4：策略条件满足？
        if check_buy_condition(quote):
            signal = {
                "symbol": symbol,
                "side": "BUY",
                "quantity": calculate_quantity(quote.last_done),
                "price": quote.last_done
            }
            signals.append(signal)

    return signals
```

### **4. 风险控制**

```python
async def execute_signal(signal, account):
    # 检查资金是否充足
    required_cash = signal["price"] * signal["quantity"]
    available_cash = account["cash"].get(currency, 0)

    if required_cash > available_cash:
        logger.warning("资金不足")
        return

    # 提交订单
    order = await trade_client.submit_order(signal)
```

---

## 📊 系统监控输出

### **正常运行时的输出**

```
============================================================
第 5 轮扫描 - 10:40:23
============================================================
⏰ 检查交易时段: 港股早盘 ✅
📊 获取实时行情: 6个标的
💰 账户余额: HKD $95,091.60 | USD $45,165.83
📦 当前持仓: 2个标的
  - 09988.HK: 28股 | 成本价 $175.30 | 市值 $4,908.40
  - AAPL.US:  19股 | 成本价 $254.43 | 市值 $4,834.17

🎯 信号生成:
  ✅ 03690.HK: 买入信号 - 48股 @ $102.30
  ⏭️  09988.HK: 已持有，跳过
  ⏭️  AAPL.US: 已持有，跳过

💤 本轮执行:
  🔄 [模拟] BUY 03690.HK: 48股 @ $102.30

⏳ 等待60秒进入下一轮...
```

### **触发风控时的输出**

```
  ⚠️  MSFT.US: 资金不足 (需要 $5,146.00, 可用 $165.83)
  ⚠️  01810.HK: 已达最大持仓数 (5/5)
  ⚠️  NVDA.US: 今日已交易
```

---

## ⚙️ 自定义配置

### **修改交易参数**

在 `scripts/realtime_auto_trading_example.py` 中：

```python
class RealtimeAutoTrader:
    def __init__(self, dry_run=True):
        # 修改这些参数
        self.budget_per_stock = 5000   # 每只股票预算
        self.max_positions = 5          # 最大持仓数量
        self.scan_interval = 60         # 扫描间隔（秒）
```

### **修改策略条件**

```python
def check_buy_condition(self, quote):
    # 自定义你的买入条件
    # 例如：RSI < 30（超卖）
    # 例如：价格突破20日均线
    # 例如：成交量放大
    pass
```

---

## 🛡️ 风险控制建议

### **资金管理**
- ✅ 单只股票不超过总资金的10%
- ✅ 总持仓不超过总资金的70%
- ✅ 保留30%现金应对市场波动

### **持仓管理**
- ✅ 最多持有5-10只股票
- ✅ 单只股票最多买入3次
- ✅ 及时止损（亏损超过5%）

### **交易频率**
- ✅ 每只股票每天最多交易1次
- ✅ 总交易次数每天不超过10次
- ✅ 避免频繁交易产生高额手续费

---

## 📈 进阶功能

### **1. 添加技术指标**

```python
# 计算RSI
def calculate_rsi(prices, period=14):
    # ... RSI计算逻辑

# 在策略中使用
if calculate_rsi(historical_prices) < 30:
    # 超卖，买入信号
```

### **2. 多策略组合**

```python
signals = []

# 策略1：RSI反转
signals.extend(rsi_strategy.generate_signals(quotes))

# 策略2：均线交叉
signals.extend(ma_cross_strategy.generate_signals(quotes))

# 策略3：成交量突破
signals.extend(volume_strategy.generate_signals(quotes))

# 合并并去重
final_signals = merge_signals(signals)
```

### **3. WebSocket实时推送**

```python
def on_quote(symbol: str, event: PushQuote):
    """实时行情推送回调"""
    # 实时处理行情更新
    process_quote_update(symbol, event)

# 订阅实时推送
ctx.set_on_quote(on_quote)
ctx.subscribe(symbols, [SubType.Quote])
```

---

## 🐛 常见问题

### **Q1: 为什么没有生成交易信号？**
**A**: 检查以下几点：
1. 是否在交易时段？
2. 自选股配置是否正确？
3. 账户是否有足够资金？
4. 是否已达持仓上限？
5. 策略条件是否过于严格？

### **Q2: 如何查看历史交易记录？**
**A**:
```python
# 查询今日订单
orders = await trade_client.today_orders()

# 查询历史订单
orders = await trade_client.history_orders(
    start_at=datetime.now() - timedelta(days=7),
    end_at=datetime.now()
)
```

### **Q3: 如何实现止损止盈？**
**A**:
```python
# 在持仓监控中添加
for symbol, position in account["positions"].items():
    pnl_pct = (current_price - position["cost"]) / position["cost"] * 100

    # 止损：亏损超过5%
    if pnl_pct < -5:
        await submit_sell_order(symbol, position["quantity"])

    # 止盈：盈利超过10%
    if pnl_pct > 10:
        await submit_sell_order(symbol, position["quantity"])
```

---

## 📞 获取帮助

如果需要：
1. ✅ 实现更复杂的策略
2. ✅ 添加WebSocket实时推送
3. ✅ 实现完整的风控系统
4. ✅ 添加订单状态监控
5. ✅ 实现回测系统

请告诉我你的具体需求！

---

## ⚠️ 重要提示

1. **先用模拟模式测试**，确认策略逻辑正确
2. **小额资金开始**，逐步增加投入
3. **密切监控运行**，及时发现问题
4. **设置止损止盈**，控制风险
5. **遵守交易规则**，合规运营

---

## 🎉 现在开始

```bash
# 运行示例（模拟模式）
python3 scripts/realtime_auto_trading_example.py

# 按 Ctrl+C 停止
```

祝交易顺利！ 🚀📈