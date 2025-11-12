# 买入信号预检查机制 (2025-11-05)

## 问题描述

之前的系统在**执行阶段**才发现资金不足或可买数量为0，导致：

1. **错误信息不明确**：只显示"订单执行失败"，用户不知道具体原因
2. **缺乏指导**：没有告诉用户当前持仓情况和下一步该做什么
3. **浪费资源**：明知道买不了还生成信号和尝试执行

### 之前的错误提示

```
❌ 订单执行失败
标的: 688.HK
类型: BUY
评分: 51
价格: $12.82
错误: 订单被拒绝或未成交

❌ 订单执行失败
标的: PLTR.US
类型: BUY
评分: 50
价格: $185.06
错误: 可买数量为0（Fallback也失败）
```

用户不知道：
- 为什么资金不足？
- 有哪些持仓？
- 哪些持仓可以考虑卖出？
- 账户的整体情况如何？

---

## 解决方案

在**信号生成阶段**就进行预检查，提前判断是否可以买入：

1. **预检查可买数量**：在生成买入信号后、发送到队列前，调用API检查可买数量
2. **持仓分析**：如果可买数量为0，立即分析当前持仓情况
3. **Slack通知**：将详细的持仓分析发送到Slack，帮助用户做决策
4. **跳过信号**：预检查不通过时，不生成买入信号，避免无意义的执行尝试

---

## 实现细节

### 1. 添加预检查方法 (scripts/signal_generator.py)

#### 1.1 初始化Slack通知器

```python
# 在 run() 方法中
slack_url = str(self.settings.slack_webhook_url) if self.settings.slack_webhook_url else None
discord_url = str(self.settings.discord_webhook_url) if self.settings.discord_webhook_url else None

async with QuoteDataClient(self.settings) as quote_client, \
           LongportTradingClient(self.settings) as trade_client, \
           MultiChannelNotifier(slack_webhook_url=slack_url, discord_webhook_url=discord_url) as slack:

    self.quote_client = quote_client
    self.trade_client = trade_client
    self.slack = slack
```

#### 1.2 预检查可买数量

```python
async def _check_buying_power_before_signal(
    self,
    symbol: str,
    current_price: float,
    signal_score: int
) -> tuple[bool, Optional[str]]:
    """
    在生成买入信号前检查可买数量

    Returns:
        (can_buy, analysis_message): 是否可以买入，以及分析消息（如果不能买入）
    """
    # 调用 API 预估可买数量
    estimate = await self.trade_client.estimate_max_purchase_quantity(
        symbol=symbol,
        order_type=openapi.OrderType.Limit,
        side=openapi.OrderSide.Buy,
        price=Decimal(str(current_price))
    )

    max_qty = estimate.cash_max_qty if hasattr(estimate, 'cash_max_qty') else 0

    if max_qty <= 0:
        # 分析持仓并发送通知
        analysis_msg = await self._analyze_and_notify_positions(
            symbol=symbol,
            current_price=current_price,
            signal_score=signal_score
        )
        return False, analysis_msg

    return True, None
```

#### 1.3 持仓分析与通知

```python
async def _analyze_and_notify_positions(
    self,
    symbol: str,
    current_price: float,
    signal_score: int
) -> str:
    """
    分析当前持仓并发送到 Slack
    """
    # 1. 获取账户信息和持仓
    account = await self.trade_client.get_account()
    positions_resp = await self.trade_client.stock_positions()

    # 2. 分析每个持仓
    #    - 标的代码
    #    - 持仓数量
    #    - 成本价 vs 当前价
    #    - 盈亏百分比
    #    - 市值占比

    # 3. 构建分析消息
    analysis_msg = f"""
    💰 **资金不足 - 无法买入 {symbol}**

    📊 **买入信号详情**:
      • 标的: {symbol}
      • 价格: ${current_price:.2f}
      • 评分: {signal_score}/100

    💼 **账户状态**:
      • HKD: 现金=$XXX, 购买力=$XXX
      • USD: 现金=$XXX, 购买力=$XXX

    📦 **当前持仓** (10个):
      🟢 **AAPL.US**: 100股 @ $150.00 → $155.00 (+3.3%) | 市值=$15,500 (25.0%)
      🔴 **BABA.US**: 200股 @ $80.00 → $75.00 (-6.3%) | 市值=$15,000 (24.2%)
      ...

    💡 **建议**:
      • 考虑减仓释放购买力
      • 或等待账户资金补充
      • 或调整仓位配置
    """

    # 4. 发送到 Slack
    await self.slack.send(analysis_msg)
```

#### 1.4 集成到信号生成流程

```python
# 在 analyze_symbol_and_generate_signal() 方法中
signal = self._analyze_buy_signals(symbol, current_price, quote, indicators, closes, highs, lows)

# 🔥 买入前预检查
if signal and signal.get('type') in ['BUY', 'WEAK_BUY']:
    signal_score = signal.get('score', 0)
    can_buy, analysis_msg = await self._check_buying_power_before_signal(
        symbol=symbol,
        current_price=current_price,
        signal_score=signal_score
    )

    if not can_buy:
        # 预检查失败，不生成买入信号
        logger.warning(f"  ⏭️  {symbol}: 预检查失败，跳过买入信号生成")
        return None

return signal
```

---

## 新的用户体验

### 场景1: 资金充足

```
📊 分析 AAPL.US
  ✅ AAPL.US: 可买数量 100 股
  📈 综合评分: 65/100
  ✅ 信号已发送到队列: BUY, 评分=65
```

### 场景2: 资金不足

```
📊 分析 PLTR.US
  ⚠️ PLTR.US: 预估可买数量为0，将分析持仓情况
  ✅ 持仓分析已发送到 Slack
  ⏭️  PLTR.US: 预检查失败，跳过买入信号生成
```

**Slack 通知内容**:

```
💰 **资金不足 - 无法买入 PLTR.US**

📊 **买入信号详情**:
  • 标的: PLTR.US
  • 价格: $185.06
  • 评分: 50/100

💼 **账户状态**:
  • HKD: 现金=$0, 购买力=-$50,000
  • USD: 现金=$1,234, 购买力=$0

📦 **当前持仓** (8个):
  🟢 **AAPL.US**: 100股 @ $150.00 → $155.00 (+3.3%) | 市值=$15,500 (25.0%)
  🔴 **TSLA.US**: 50股 @ $250.00 → $240.00 (-4.0%) | 市值=$12,000 (19.4%)
  🟢 **MSFT.US**: 80股 @ $300.00 → $310.00 (+3.3%) | 市值=$24,800 (40.0%)
  🔴 **META.US**: 30股 @ $400.00 → $390.00 (-2.5%) | 市值=$11,700 (18.9%)
  ... 还有 4 个持仓

💡 **建议**:
  • 考虑减仓释放购买力
  • 或等待账户资金补充
  • 或调整仓位配置
```

---

## 效果对比

### 之前
❌ 在执行阶段失败
❌ 只显示错误消息
❌ 用户不知道该怎么办
❌ 浪费API调用和执行资源

### 现在
✅ 在信号生成阶段预检查
✅ 详细分析持仓情况
✅ 提供明确的操作建议
✅ 避免无意义的信号生成和执行

---

## 配置要求

确保在 `.env` 文件或 `configs/settings.toml` 中配置了 Slack Webhook URL:

```bash
# .env
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

或

```toml
# configs/settings.toml
slack_webhook_url = "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
```

---

## 代码变更

### 修改的文件

1. **scripts/signal_generator.py**:
   - 添加 `MultiChannelNotifier` 导入
   - 在 `run()` 中初始化 Slack 通知器
   - 新增 `_check_buying_power_before_signal()` 方法
   - 新增 `_analyze_and_notify_positions()` 方法
   - 在 `analyze_symbol_and_generate_signal()` 中集成预检查逻辑

### 核心逻辑

```
信号生成流程（之前）:
  分析技术指标 → 生成信号 → 发送到队列 → 执行订单 → ❌ 失败

信号生成流程（现在）:
  分析技术指标 → 生成信号 → 预检查可买数量 →
    ├─ 可以买入 → 发送到队列 → 执行订单 → ✅ 成功
    └─ 不能买入 → 分析持仓 → 发送Slack通知 → ⏭️ 跳过信号
```

---

## 总结

这次改进将资金检查从**执行阶段**提前到**信号生成阶段**，实现了：

1. **提前预判**：在生成信号前就知道能否买入
2. **详细分析**：自动分析持仓情况并提供建议
3. **主动通知**：通过Slack及时告知用户
4. **节省资源**：避免生成无法执行的信号

用户现在可以在收到通知后，根据持仓分析做出明智的决策，而不是看到一堆"订单执行失败"的错误消息。
