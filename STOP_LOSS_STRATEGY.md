# 止损止盈与技术指标冲突处理策略

## 问题分析

当持仓同时满足多个平仓条件时，如何决策？

### 典型冲突场景

#### 场景1: 技术指标建议卖出 vs 未到止盈
```
入场价: $100
当前价: $108 (+8%)
止盈位: $115 (+15%)
RSI: 75 (超买)
价格: 突破布林带上轨

冲突:
✅ 技术指标: 超买，建议卖出
❌ 止盈位: 还差7%才到止盈

如何决策？
```

#### 场景2: 小幅盈利 vs 技术反转
```
入场价: $100
当前价: $103 (+3%)
止损位: $94 (-6%)
止盈位: $115 (+15%)
RSI: 72 (刚进入超买)
MACD: 死叉

冲突:
✅ 小幅盈利 (+3%)
⚠️ 技术指标开始反转
❌ 未到止盈位

如何决策？
```

#### 场景3: 止损位 vs 技术指标仍然强势
```
入场价: $100
当前价: $93 (-7%)
止损位: $94 (-6%)
RSI: 35 (接近超卖，可能反弹)
布林带: 接近下轨
MACD: 开始金叉

冲突:
❌ 触及止损 (-7%)
✅ 技术指标显示可能反弹

如何决策？
```

---

## 当前系统的处理方式

### 优先级顺序（硬编码）

```python
async def check_exit_signals(self):
    # 1. 最高优先级: 止损 (无条件执行)
    if current_price <= stop_loss:
        await self._execute_sell(symbol, "止损")
        return  # 立即返回，不再检查其他条件

    # 2. 次高优先级: 止盈 (无条件执行)
    if current_price >= take_profit:
        await self._execute_sell(symbol, "止盈")
        return  # 立即返回

    # 3. 最低优先级: 技术指标
    if RSI > 70 and price > BB_upper:
        await self._execute_sell(symbol, "技术反转")
```

### 问题

1. **过于机械** - 不考虑市场环境
2. **错失机会** - 技术指标强势时仍止损
3. **过早平仓** - 小幅盈利时遇到技术反转就卖出
4. **缺乏灵活性** - 无法根据情况调整

---

## 改进方案

### 方案1: 智能决策系统（推荐）⭐

引入**决策评分机制**，综合考虑多个因素：

```python
async def check_exit_signals_smart(self, symbol, position):
    """
    智能平仓决策系统

    评分因素:
    1. 价格距离止损/止盈的位置
    2. 当前盈亏百分比
    3. 技术指标的强弱
    4. 持仓时间
    5. 市场波动率
    """

    # 获取当前状态
    current_price = get_price(symbol)
    entry_price = position.entry_price
    stop_loss = position.stop_loss
    take_profit = position.take_profit
    pnl_pct = (current_price / entry_price - 1) * 100

    # 计算各维度评分
    stop_score = self._calculate_stop_score(
        current_price, stop_loss, take_profit, pnl_pct
    )
    technical_score = self._calculate_technical_score(symbol)
    time_score = self._calculate_time_score(position)

    # 综合决策
    total_score = stop_score + technical_score + time_score

    # 决策阈值
    if total_score >= 80:
        return "SELL_IMMEDIATELY"  # 立即卖出
    elif total_score >= 60:
        return "SELL_WHEN_PROFITABLE"  # 盈利时卖出
    elif total_score >= 40:
        return "REDUCE_POSITION"  # 减仓50%
    else:
        return "HOLD"  # 继续持有


def _calculate_stop_score(self, current, stop_loss, take_profit, pnl_pct):
    """
    计算止损止盈评分 (0-50分)

    逻辑:
    - 触及止损: 50分 (最高)
    - 接近止损 (5%内): 40-50分
    - 触及止盈: 50分 (最高)
    - 接近止盈 (5%内): 40-50分
    - 小幅盈利 (0-5%): 10-20分
    - 大幅盈利 (>10%): 30-40分
    """
    score = 0

    # 止损评分
    if current <= stop_loss:
        score = 50  # 触及止损
    elif current <= stop_loss * 1.05:
        # 接近止损，根据距离计算
        distance = (current - stop_loss) / (entry_price - stop_loss)
        score = 40 + (1 - distance) * 10

    # 止盈评分
    elif current >= take_profit:
        score = 50  # 触及止盈
    elif current >= take_profit * 0.95:
        # 接近止盈
        distance = (take_profit - current) / (take_profit - entry_price)
        score = 40 + (1 - distance) * 10

    # 盈利评分
    elif pnl_pct > 10:
        score = 30 + min(pnl_pct - 10, 10)  # 大幅盈利 30-40分
    elif pnl_pct > 5:
        score = 20 + (pnl_pct - 5)  # 中等盈利 20-30分
    elif pnl_pct > 0:
        score = 10 + pnl_pct * 2  # 小幅盈利 10-20分
    else:
        score = max(0, 10 + pnl_pct)  # 亏损 0-10分

    return score


def _calculate_technical_score(self, symbol):
    """
    计算技术指标评分 (0-30分)

    逻辑:
    - RSI超买 + 布林带上轨: 30分 (强烈卖出)
    - RSI超买: 20分
    - MACD死叉: 15分
    - 均线死叉: 10分
    - 技术指标中性: 0分
    - 技术指标强势: -10分 (不建议卖出)
    """
    score = 0

    # 获取技术指标
    rsi, bb_upper, macd_hist, sma_20, sma_50 = self._get_indicators(symbol)

    # RSI评分
    if rsi > 80:
        score += 30  # 极度超买
    elif rsi > 70:
        score += 20  # 超买
    elif rsi < 30:
        score -= 10  # 超卖，不建议卖

    # 布林带评分
    if current_price > bb_upper * 1.02:
        score += 10  # 突破上轨

    # MACD评分
    if macd_hist < 0 and prev_macd_hist > 0:
        score += 15  # 死叉
    elif macd_hist < 0:
        score += 5  # 空头

    # 均线评分
    if sma_20 < sma_50:
        score += 10  # 死叉

    return min(30, max(-10, score))


def _calculate_time_score(self, position):
    """
    计算持仓时间评分 (0-20分)

    逻辑:
    - 持仓 > 30天: 20分 (过长)
    - 持仓 > 20天: 15分
    - 持仓 > 10天: 10分
    - 持仓 > 5天: 5分
    - 持仓 < 5天: 0分
    """
    days = (datetime.now() - position.entry_time).days

    if days > 30:
        return 20
    elif days > 20:
        return 15
    elif days > 10:
        return 10
    elif days > 5:
        return 5
    else:
        return 0
```

### 实际决策示例

#### 示例1: 小幅盈利 + 技术反转

```python
状态:
  入场价: $100
  当前价: $103 (+3%)
  止损位: $94
  止盈位: $115
  RSI: 72 (超买)
  MACD: 死叉

评分:
  止损止盈评分: 15分 (小幅盈利)
  技术指标评分: 20分 (RSI超买) + 15分 (MACD死叉) = 35分
  持仓时间评分: 0分 (2天)
  总分: 15 + 35 + 0 = 50分

决策: REDUCE_POSITION (减仓50%)
原因: 有盈利但未到止盈，技术指标反转，减半仓位锁定部分利润
```

#### 示例2: 触及止损 + 技术强势

```python
状态:
  入场价: $100
  当前价: $93 (-7%)
  止损位: $94
  RSI: 28 (超卖，可能反弹)
  MACD: 金叉
  均线: 多头排列

评分:
  止损止盈评分: 50分 (触及止损)
  技术指标评分: -10分 (超卖 + MACD金叉)
  持仓时间评分: 0分
  总分: 50 + (-10) + 0 = 40分

决策: REDUCE_POSITION (减仓50%)
原因: 虽然触及止损，但技术指标显示可能反弹，减半仓位控制风险同时保留反弹机会
```

#### 示例3: 接近止盈 + 技术超买

```python
状态:
  入场价: $100
  当前价: $112 (+12%)
  止盈位: $115 (+15%)
  RSI: 78 (超买)
  价格: 突破布林带上轨

评分:
  止损止盈评分: 45分 (接近止盈)
  技术指标评分: 30分 (极度超买 + 突破上轨)
  持仓时间评分: 5分 (7天)
  总分: 45 + 30 + 5 = 80分

决策: SELL_IMMEDIATELY (立即卖出)
原因: 接近止盈 + 技术指标极度超买，立即平仓锁定利润
```

---

## 方案2: 条件止损（Trailing Stop）

动态调整止损位，锁定利润：

```python
async def update_trailing_stop(self, symbol, position):
    """
    移动止损

    规则:
    - 盈利 > 5%: 止损上移到入场价 (保本)
    - 盈利 > 10%: 止损上移到 +5% (锁定5%利润)
    - 盈利 > 15%: 止损上移到 +10% (锁定10%利润)
    """
    current_price = get_price(symbol)
    entry_price = position.entry_price
    pnl_pct = (current_price / entry_price - 1) * 100

    new_stop_loss = position.stop_loss

    if pnl_pct >= 15:
        # 锁定10%利润
        new_stop_loss = entry_price * 1.10
    elif pnl_pct >= 10:
        # 锁定5%利润
        new_stop_loss = entry_price * 1.05
    elif pnl_pct >= 5:
        # 保本
        new_stop_loss = entry_price

    # 只向上移动止损，不向下
    if new_stop_loss > position.stop_loss:
        position.stop_loss = new_stop_loss
        logger.info(
            f"📍 {symbol} 移动止损: "
            f"${position.stop_loss:.2f} → ${new_stop_loss:.2f} "
            f"(锁定利润 {(new_stop_loss/entry_price-1)*100:.1f}%)"
        )
```

---

## 方案3: 分批平仓

根据不同信号分批退出：

```python
async def partial_exit(self, symbol, position):
    """
    分批平仓

    规则:
    - 技术指标反转: 卖出30%
    - 盈利10%: 卖出30%
    - 盈利15%: 卖出剩余40%
    - 止损: 全部卖出
    """
    current_price = get_price(symbol)
    entry_price = position.entry_price
    pnl_pct = (current_price / entry_price - 1) * 100

    remaining = position.quantity

    # 止损: 全部卖出
    if current_price <= position.stop_loss:
        await sell(symbol, remaining, "止损")
        return

    # 盈利15%: 卖出40%
    if pnl_pct >= 15 and not position.sold_40pct:
        qty = int(remaining * 0.4)
        await sell(symbol, qty, "止盈40%")
        position.sold_40pct = True
        remaining -= qty

    # 盈利10%: 卖出30%
    if pnl_pct >= 10 and not position.sold_30pct:
        qty = int(remaining * 0.3)
        await sell(symbol, qty, "止盈30%")
        position.sold_30pct = True
        remaining -= qty

    # 技术反转: 卖出30%
    if self._check_technical_reversal(symbol) and not position.sold_tech:
        qty = int(remaining * 0.3)
        await sell(symbol, qty, "技术反转减仓")
        position.sold_tech = True
```

---

## 推荐配置

### 保守策略
```python
# 严格执行止损止盈，技术指标作为辅助
class ConservativeStrategy:
    def __init__(self):
        self.stop_loss_priority = "HIGH"  # 止损优先
        self.take_profit_priority = "HIGH"  # 止盈优先
        self.technical_priority = "LOW"  # 技术指标辅助

        # 止损不调整
        self.use_trailing_stop = False

        # 技术指标需要极端条件才平仓
        self.technical_exit_threshold = 80  # RSI > 80
```

### 平衡策略（推荐）⭐
```python
# 止损止盈 + 智能决策
class BalancedStrategy:
    def __init__(self):
        self.use_smart_decision = True  # 启用智能决策
        self.use_trailing_stop = True  # 启用移动止损

        # 决策阈值
        self.sell_immediately_threshold = 80
        self.sell_profitable_threshold = 60
        self.reduce_position_threshold = 40

        # 移动止损参数
        self.trailing_stop_trigger = 5  # 盈利5%后启动
        self.trailing_stop_distance = 5  # 保持5%距离
```

### 激进策略
```python
# 更多依赖技术指标
class AggressiveStrategy:
    def __init__(self):
        self.stop_loss_priority = "MEDIUM"
        self.technical_priority = "HIGH"  # 技术指标优先

        # 技术指标敏感
        self.technical_exit_threshold = 70  # RSI > 70

        # 分批平仓
        self.use_partial_exit = True
        self.partial_exit_levels = [0.1, 0.15, 0.20]  # 10%, 15%, 20%
```

---

## 实现建议

### 1. 添加决策日志
```python
logger.info(f"""
{symbol} 平仓决策分析:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
当前状态:
  价格: ${current_price:.2f} ({pnl_pct:+.2f}%)
  入场: ${entry_price:.2f}
  止损: ${stop_loss:.2f}
  止盈: ${take_profit:.2f}

评分明细:
  止损止盈: {stop_score}分
  技术指标: {tech_score}分
  持仓时间: {time_score}分
  ────────────────
  总分: {total_score}分

决策: {decision}
原因: {reason}
""")
```

### 2. 可配置的决策模式
```python
# 在配置文件中设置
exit_strategy:
  mode: "smart"  # "strict" | "smart" | "trailing"
  technical_weight: 0.3  # 技术指标权重
  stop_weight: 0.5  # 止损止盈权重
  time_weight: 0.2  # 时间权重
```

### 3. 回测验证
```python
# 对比不同策略的效果
backtest_results = {
    "strict": {  # 严格止损止盈
        "win_rate": 0.65,
        "avg_profit": 0.08,
        "max_drawdown": -0.12
    },
    "smart": {  # 智能决策
        "win_rate": 0.68,
        "avg_profit": 0.11,
        "max_drawdown": -0.09
    },
    "trailing": {  # 移动止损
        "win_rate": 0.62,
        "avg_profit": 0.15,
        "max_drawdown": -0.11
    }
}
```

---

## 总结

### 当前系统（基础版）
```
✅ 优点: 简单明了，风险可控
❌ 缺点: 机械，可能错失机会
```

### 推荐升级（智能决策）
```
✅ 优点: 灵活，综合考虑多因素
✅ 优点: 减少过早平仓
✅ 优点: 锁定更多利润
⚠️  缺点: 复杂度增加
```

### 最佳实践
```
1. 保留基础止损止盈作为底线
2. 使用智能决策优化平仓时机
3. 引入移动止损锁定利润
4. 通过回测验证策略有效性
5. 根据市场环境调整参数
```

---

**建议**: 先使用基础版运行1-2周，熟悉后再升级到智能决策版本。