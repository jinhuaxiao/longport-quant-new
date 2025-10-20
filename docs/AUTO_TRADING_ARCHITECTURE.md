# 自动交易系统完整架构

## 🎯 核心流程

```
┌─────────────────────────────────────────────────────────────────┐
│                        自动交易系统                              │
└─────────────────────────────────────────────────────────────────┘

第一步：历史数据准备（一次性初始化）
├── 1. 同步标的基本信息
├── 2. 同步历史日线数据（回测用）
├── 3. 同步历史分钟数据（近期数据）
└── 4. 同步交易日历

第二步：实时交易循环
├── 1. 获取实时行情（推送/轮询）
├── 2. 更新技术指标
├── 3. 执行交易策略（生成信号）
├── 4. 风险检查
│   ├── 账户资金检查
│   ├── 持仓检查
│   ├── 单日交易次数限制
│   └── 最大持仓限制
├── 5. 下单执行
└── 6. 更新持仓状态

第三步：持续监控
├── 1. 订单状态跟踪
├── 2. 持仓盈亏监控
└── 3. 风险预警
```

---

## 📦 模块详解

### 1️⃣ **数据初始化模块（一次性）**

#### **脚本**：`scripts/init_system.py`

```python
# 初始化历史数据
python3 scripts/init_system.py
```

**功能**：
- ✅ 同步标的基本信息（SecurityStatic表）
- ✅ 同步历史日线（30-90天）
- ✅ 同步历史分钟线（3-7天）
- ✅ 同步交易日历
- ✅ 初始化数据库表结构

---

### 2️⃣ **实时行情模块**

#### **当前实现**：
**文件**：`src/longport_quant/data/quote_client.py`

**两种方式**：

##### **方式A：轮询模式（当前使用）**
```python
# 每分钟定时获取行情
async def get_latest_quotes(symbols):
    quotes = await quote_client.get_realtime_quote(symbols)
    return quotes
```

**优点**：
- ✅ 简单可靠
- ✅ 适合中低频策略（分钟级）
- ✅ 不需要长连接

**缺点**：
- ❌ 有延迟（1分钟）
- ❌ API调用频率限制

##### **方式B：推送模式（需要实现）**
```python
# WebSocket实时推送
def on_quote(symbol: str, event: PushQuote):
    # 实时处理行情更新
    process_quote_update(symbol, event)

ctx.set_on_quote(on_quote)
ctx.subscribe(symbols, [SubType.Quote])
```

**优点**：
- ✅ 实时性强（毫秒级）
- ✅ 适合高频策略
- ✅ 节省API调用次数

**缺点**：
- ❌ 需要维护长连接
- ❌ 需要处理断线重连
- ❌ 港股BMP权限无推送

---

### 3️⃣ **账户资金&持仓查询模块**

#### **已实现**：
**文件**：`src/longport_quant/execution/client.py`

```python
class LongportTradingClient:
    # 查询账户余额
    async def account_balance(self, currency: str | None = None)

    # 查询股票持仓
    async def stock_positions(self, symbols: Optional[List[str]] = None)

    # 查询资金流水
    async def cash_flow(self, start_at, end_at)

    # 估算最大可买数量
    async def estimate_max_purchase_quantity(
        self, symbol, order_type, side, price
    )
```

**使用示例**：
```python
# 查询账户余额
balances = await trading_client.account_balance()
for balance in balances:
    print(f"{balance.currency}: {balance.cash}")

# 查询持仓
positions = await trading_client.stock_positions()
for pos in positions.channels:
    for stock in pos.positions:
        print(f"{stock.symbol}: {stock.quantity}股")
```

---

### 4️⃣ **策略判断模块**

#### **已实现策略**：

##### **A. 自选股自动交易策略**
**文件**：`src/longport_quant/strategies/watchlist_auto.py`

```python
class AutoTradeStrategy:
    """早盘开盘时自动买入自选股"""

    async def on_quote(self, quote: dict):
        # 1. 检查是否在交易时段
        # 2. 检查今天是否已交易
        # 3. 计算购买数量（根据预算）
        # 4. 生成买入信号
```

##### **B. 技术指标策略**
**已有策略**：
- `rsi_reversal.py` - RSI反转策略
- `ma_crossover.py` - 均线交叉策略
- `bollinger_bands.py` - 布林带策略
- `volume_breakout.py` - 成交量突破策略
- `multi_timeframe_momentum.py` - 多周期动量策略

---

### 5️⃣ **风险控制模块**

#### **已实现**：
**文件**：`src/longport_quant/risk/checks.py`

```python
class RiskEngine:
    def check_order(self, signal):
        # 1. 检查单笔订单金额
        # 2. 检查持仓数量限制
        # 3. 检查日内交易次数
        # 4. 检查总风险敞口
```

**风险参数**：
```python
risk_limits = {
    "max_position_per_symbol": 10000,  # 单个标的最大持仓金额
    "max_total_exposure": 100000,       # 总风险敞口
    "max_daily_trades": 10,             # 单日最大交易次数
    "max_order_size": 5000,             # 单笔订单最大金额
}
```

---

### 6️⃣ **订单执行模块**

#### **已实现**：
**文件**：`src/longport_quant/execution/order_router.py`

```python
class OrderRouter:
    async def submit_order(self, signal):
        # 1. 风险检查
        # 2. 查询可用资金
        # 3. 计算实际下单数量
        # 4. 提交订单
        # 5. 记录订单
```

**订单类型**：
- ✅ 市价单 (Market Order)
- ✅ 限价单 (Limit Order)
- ✅ 止损单 (Stop Loss)
- ✅ 条件单 (Conditional Order)

---

## 🔄 完整的自动交易流程示例

### **场景：基于实时行情的自动交易**

```python
class RealtimeAutoTrading:
    """实时自动交易系统"""

    def __init__(self):
        self.quote_client = QuoteDataClient(settings)
        self.trading_client = LongportTradingClient(settings)
        self.risk_engine = RiskEngine(settings)
        self.portfolio = PortfolioService(db)

    async def run(self):
        """主循环"""
        while True:
            # 1. 获取实时行情
            quotes = await self.get_realtime_quotes()

            # 2. 检查账户状态
            account = await self.check_account_status()

            # 3. 执行策略判断
            signals = await self.run_strategies(quotes, account)

            # 4. 风险检查
            approved_signals = self.risk_check(signals, account)

            # 5. 执行订单
            for signal in approved_signals:
                await self.execute_order(signal)

            # 6. 更新持仓
            await self.update_portfolio()

            # 等待下一个周期
            await asyncio.sleep(60)  # 1分钟

    async def get_realtime_quotes(self):
        """获取实时行情"""
        symbols = self.get_watchlist_symbols()
        quotes = await self.quote_client.get_realtime_quote(symbols)
        return quotes

    async def check_account_status(self):
        """检查账户状态"""
        # 查询余额
        balances = await self.trading_client.account_balance()

        # 查询持仓
        positions = await self.trading_client.stock_positions()

        return {
            "cash": self._extract_cash(balances),
            "positions": self._parse_positions(positions),
            "buying_power": self._calculate_buying_power(balances)
        }

    async def run_strategies(self, quotes, account):
        """执行策略"""
        signals = []

        for quote in quotes:
            # 策略1：RSI反转
            if self._rsi_reversal_signal(quote):
                signal = Signal(
                    symbol=quote.symbol,
                    side="BUY",
                    price=quote.last_done,
                    quantity=self._calculate_quantity(
                        quote.last_done,
                        account["buying_power"]
                    )
                )
                signals.append(signal)

            # 策略2：均线交叉
            if self._ma_cross_signal(quote):
                # ...

        return signals

    def risk_check(self, signals, account):
        """风险检查"""
        approved = []

        for signal in signals:
            # 1. 检查资金是否充足
            required_cash = signal.price * signal.quantity
            if required_cash > account["cash"]:
                logger.warning(f"资金不足: {signal.symbol}")
                continue

            # 2. 检查持仓限制
            current_position = account["positions"].get(signal.symbol, 0)
            if current_position + signal.quantity > MAX_POSITION:
                logger.warning(f"持仓超限: {signal.symbol}")
                continue

            # 3. 风险引擎检查
            if not self.risk_engine.check_order(signal):
                logger.warning(f"风险检查未通过: {signal.symbol}")
                continue

            approved.append(signal)

        return approved

    async def execute_order(self, signal):
        """执行订单"""
        try:
            order = await self.trading_client.submit_order({
                "symbol": signal.symbol,
                "side": signal.side,
                "quantity": signal.quantity,
                "price": signal.price
            })

            logger.info(f"订单已提交: {order['order_id']}")

            # 记录订单
            await self._record_order(order)

        except Exception as e:
            logger.error(f"下单失败: {e}")

    async def update_portfolio(self):
        """更新持仓状态"""
        positions = await self.trading_client.stock_positions()
        await self.portfolio.update_positions(positions)
```

---

## 📋 实现清单

### ✅ **已实现的功能**

1. **数据层**
   - ✅ 历史K线同步
   - ✅ 实时行情获取（轮询）
   - ✅ 交易日历管理
   - ✅ 标的信息管理

2. **交易层**
   - ✅ 账户余额查询
   - ✅ 持仓查询
   - ✅ 订单提交/取消/改单
   - ✅ 订单状态查询
   - ✅ 成交查询

3. **策略层**
   - ✅ 自选股自动交易
   - ✅ 5个技术指标策略
   - ✅ 策略基类框架

4. **风险控制**
   - ✅ 基础风险检查
   - ✅ 持仓限制
   - ✅ 订单金额限制

### 🔨 **需要完善的功能**

1. **实时行情推送**
   - ❌ WebSocket长连接
   - ❌ 断线重连机制
   - ❌ 行情数据缓存

2. **策略集成**
   - ❌ 策略管理器完整实现
   - ❌ 多策略协调
   - ❌ 信号优先级管理

3. **风控增强**
   - ❌ 动态仓位管理
   - ❌ 止损/止盈自动触发
   - ❌ 风险预警通知

4. **持仓管理**
   - ❌ 实时持仓同步
   - ❌ 盈亏统计
   - ❌ 持仓分析报告

---

## 🚀 快速启动完整自动交易

### **第一步：初始化历史数据**
```bash
# 创建数据库表
python3 scripts/create_database.py

# 同步历史数据
python3 scripts/sync_historical_klines.py

# 同步交易日历
python3 scripts/sync_trading_calendar_data.py
```

### **第二步：配置自选股**
```yaml
# configs/watchlist_test.yml
markets:
  hk:
    - 09988.HK  # 阿里巴巴
    - 03690.HK  # 美团
  us:
    - AAPL      # 苹果
    - MSFT      # 微软
```

### **第三步：启动自动交易**
```bash
python3 scripts/start_auto_trading.py
```

---

## 💡 建议

### **短期（当前可用）**
1. ✅ 使用轮询模式获取实时行情（1分钟周期）
2. ✅ 使用自选股自动交易策略
3. ✅ 设置保守的风险参数

### **中期（需要完善）**
1. 🔨 实现WebSocket实时推送
2. 🔨 完善策略管理器
3. 🔨 增强风控系统

### **长期（高级功能）**
1. 📊 回测系统
2. 📈 绩效分析
3. 🤖 机器学习策略
4. 📱 移动端监控

---

## ⚠️ 风险提示

1. **资金安全**：建议先用小额资金测试
2. **权限检查**：确保有正确的行情和交易权限
3. **监控运行**：密切关注系统运行状态
4. **风险控制**：设置合理的止损止盈
5. **合规性**：遵守交易所规则和监管要求

---

## 📞 需要实现的完整自动交易系统？

如果你需要我帮你实现一个完整的、带实时行情推送的自动交易系统，我可以：

1. 实现WebSocket实时行情推送
2. 集成账户资金和持仓实时查询
3. 完善策略管理器
4. 增强风险控制系统
5. 添加订单状态监控

请告诉我你的需求！