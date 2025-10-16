# 动态止损止盈系统设计 (Dynamic Stop Loss/Take Profit System)

**创建日期**: 2025-10-16
**状态**: 设计中 → 实现中

---

## 📋 需求背景

### 用户痛点
> "止盈止损是不是应该根据指标来设置止盈止盈 动态的 更好，比如有一些指标提示值得继续持有的 是不是不应该达到10% 就止盈"

### 当前问题
目前系统使用**固定百分比**止损止盈：
- 止损: -5% 或 -2.5×ATR
- 止盈: +10% 或 +3.5×ATR

**缺陷**:
1. ❌ 忽略市场趋势（强上涨趋势时过早止盈）
2. ❌ 忽略技术指标（指标显示继续持有时仍止盈）
3. ❌ 机械化执行（不考虑当前市场状态）
4. ❌ 可能错过大行情（10%就止盈，错过后续涨幅）

---

## 🎯 设计目标

### 核心原则
1. **智能延迟止盈**: 当技术指标显示强势时，延迟或提高止盈目标
2. **动态调整止损**: 根据趋势强度和波动性调整止损位
3. **指标驱动决策**: 综合RSI、MACD、布林带、趋势等多个指标
4. **保护利润**: 在趋势反转时及时止盈，而非等到固定百分比

### 决策优先级
```
优先级1: 技术指标恶化（立即止损，忽略价格）
         例如: MACD死叉 + RSI下破 → 立即平仓

优先级2: 趋势反转信号（提前止盈）
         例如: 强上涨后出现顶部信号 → 不等10%直接平仓

优先级3: 指标显示继续持有（延迟止盈）
         例如: 强上涨 + RSI 50-70 + MACD金叉 → 延迟止盈到20%或更高

优先级4: 达到固定止损位（保底逻辑）
         例如: 价格 < 止损位 → 平仓（防止极端情况）
```

---

## 📊 技术指标规则

### 1. RSI (相对强弱指标)

#### 止盈逻辑
| RSI区间 | 持仓收益 | 决策 | 原因 |
|---------|---------|------|------|
| > 80 | 任意 | **立即止盈** | 极度超买，很可能回调 |
| 70-80 | > 5% | 考虑止盈 | 超买区间，获利了结 |
| 50-70 | 5-15% | **延迟止盈** | 强势区间，趋势未完 |
| 40-50 | > 10% | 使用固定止盈 | 中性区间 |
| < 40 | > 0% | 提前止盈 | 转弱信号 |

#### 止损逻辑
| RSI区间 | 持仓收益 | 决策 | 原因 |
|---------|---------|------|------|
| < 20 | < -3% | **暂缓止损** | 极度超卖，可能反弹 |
| 20-30 | < -5% | 观察 | 超卖区间 |
| 30-40 | < -5% | 执行止损 | 弱势确认 |
| < 30 | < -8% | 执行止损 | 超过容忍度 |

---

### 2. MACD (趋势动量)

#### 金叉/死叉判断
```python
# 金叉（买入/持有信号）
if prev_macd_histogram < 0 and macd_histogram > 0:
    signal = "GOLDEN_CROSS"  # 强烈持有

# 死叉（卖出信号）
if prev_macd_histogram > 0 and macd_histogram < 0:
    signal = "DEATH_CROSS"   # 立即平仓
```

#### 决策规则
| MACD状态 | 持仓收益 | 决策 | 优先级 |
|----------|---------|------|--------|
| 刚金叉 | > 0% | **强烈持有** | 最高 |
| 柱状图扩大 | > 5% | 延迟止盈 | 高 |
| 多头但减弱 | > 10% | 考虑止盈 | 中 |
| 死叉 | 任意 | **立即止盈** | 最高 |
| 空头且扩大 | < 0% | 立即止损 | 高 |

---

### 3. 布林带 (波动通道)

#### 位置判断
```python
bb_range = bb_upper - bb_lower
bb_position = (current_price - bb_lower) / bb_range * 100

# 0%: 触及下轨
# 50%: 中轨
# 100%: 触及上轨
```

#### 决策规则
| BB位置 | 趋势 | 持仓收益 | 决策 |
|--------|------|---------|------|
| > 95% | 上涨 | 5-20% | **延迟止盈** (突破上轨，强势) |
| > 95% | 横盘 | > 10% | 立即止盈 (超买) |
| < 5% | 下跌 | < 0% | 考虑止损 (破下轨) |
| 70-95% | 上涨 | > 8% | 延迟止盈 (健康上涨) |

**BB收窄/扩张**:
- 收窄 (width < 10%): 低波动，即将突破 → 持有
- 扩张 (width > 30%): 高波动，趋势确立 → 根据方向决定

---

### 4. 趋势确认 (SMA均线)

#### 均线系统
```python
sma_20 = 20日均线
sma_50 = 50日均线

trend_strength = (current_price - sma_20) / sma_20 * 100
```

#### 趋势强度分类
| 趋势 | 条件 | 止盈策略 | 止损策略 |
|------|------|----------|----------|
| 强上涨 | price > sma_20 > sma_50, 且price > sma_20 5%以上 | **延迟至15-20%** | 追踪止损 |
| 温和上涨 | price > sma_20 > sma_50 | 延迟至12-15% | 标准止损 |
| 横盘 | sma_20 ≈ sma_50 | 标准10% | 标准止损 |
| 转弱 | price < sma_20 但 > sma_50 | 提前止盈 | 收紧止损 |
| 下跌 | price < sma_20 < sma_50 | 立即止盈 | 立即止损 |

---

## 🧠 综合决策算法

### 退出决策分数系统 (Exit Decision Scoring)

```python
def calculate_exit_score(indicators, position_info):
    """
    计算退出评分（-100 到 +100）

    负分: 应该继续持有（延迟止盈）
    正分: 应该平仓
    0分: 使用固定止损止盈

    Returns:
        score: -100 到 +100
        reason: 决策原因
        action: "HOLD" | "TAKE_PROFIT" | "STOP_LOSS" | "STANDARD"
    """
```

#### 评分规则

**持有信号（负分）**:
- 强上涨趋势 (price > sma_20 > sma_50): -30分
- MACD金叉或柱状图扩大: -25分
- RSI 50-70（强势区间）: -20分
- 突破布林带上轨: -15分
- 成交量持续放大: -10分

**平仓信号（正分）**:
- MACD死叉: +50分（立即平仓）
- RSI > 80（极度超买）: +40分
- 价格远离上轨且RSI回落: +30分
- 均线死叉 (sma_20 < sma_50): +25分
- 成交量萎缩: +15分
- 价格跌破sma_20: +20分

#### 决策逻辑
```python
if score >= 50:
    action = "TAKE_PROFIT_NOW"      # 立即止盈（忽略固定止盈位）
elif score >= 30:
    action = "TAKE_PROFIT_EARLY"    # 提前止盈（不等到10%）
elif score >= 10:
    action = "STANDARD"             # 使用固定止损止盈
elif score <= -20:
    action = "DELAY_TAKE_PROFIT"    # 延迟止盈（提高到15-20%）
elif score <= -40:
    action = "STRONG_HOLD"          # 强烈持有（暂停止盈检查）
else:
    action = "STANDARD"
```

---

## 🔧 实现方案

### 新增方法

#### 1. `_fetch_current_indicators(symbol, quote)`
获取当前技术指标（复用 `_calculate_all_indicators` 逻辑）

```python
async def _fetch_current_indicators(self, symbol, quote):
    """获取标的当前的技术指标"""
    # 获取历史K线
    # 计算RSI, MACD, BB, SMA等
    # 返回指标字典
```

#### 2. `_calculate_exit_score(indicators, position_info, stops)`
计算退出评分和决策

```python
def _calculate_exit_score(self, indicators, position_info, stops):
    """
    基于技术指标计算退出评分

    Args:
        indicators: 技术指标字典
        position_info: 持仓信息（cost_price, current_price, quantity等）
        stops: 数据库中的止损止盈设置

    Returns:
        {
            'score': int,              # -100 到 +100
            'action': str,             # HOLD | TAKE_PROFIT | STOP_LOSS | STANDARD
            'reason': str,             # 决策原因
            'adjusted_stop_loss': float,      # 调整后的止损位
            'adjusted_take_profit': float,    # 调整后的止盈位
        }
    """
```

#### 3. `check_exit_signals()` - 增强版
修改现有方法，集成智能决策

```python
async def check_exit_signals(self, quotes, account):
    """检查现有持仓的止损止盈条件（智能版）"""

    for position in positions:
        # 1. 获取当前技术指标
        indicators = await self._fetch_current_indicators(symbol, quote)

        # 2. 计算退出评分
        exit_decision = self._calculate_exit_score(indicators, position, stops)

        # 3. 根据决策执行
        if exit_decision['action'] == 'TAKE_PROFIT':
            # 生成止盈信号
        elif exit_decision['action'] == 'STOP_LOSS':
            # 生成止损信号
        elif exit_decision['action'] == 'HOLD':
            # 不生成信号，继续持有
        elif exit_decision['action'] == 'STANDARD':
            # 使用固定止损止盈逻辑（现有逻辑）
```

---

## 📈 示例场景

### 场景1: 强上涨趋势 - 延迟止盈 ✅
```
标的: 9988.HK (阿里巴巴)
入场价: $100
当前价: $110 (+10%)
固定止盈位: $110 (应该平仓)

技术指标:
- RSI: 62 (强势区间，未超买)
- MACD: 金叉后柱状图持续扩大
- 价格: 突破布林带上轨
- 趋势: price > sma_20 > sma_50

决策评分: -45分
动作: STRONG_HOLD（强烈持有）
新止盈位: $120 (+20%)
原因: "强上涨趋势 + MACD金叉 + RSI强势区间 → 延迟止盈至20%"
```

### 场景2: 极度超买 - 提前止盈 ✅
```
标的: 1810.HK (小米)
入场价: $20
当前价: $21.6 (+8%)
固定止盈位: $22 (未达到)

技术指标:
- RSI: 82 (极度超买)
- MACD: 柱状图开始收窄
- 价格: 远离布林带上轨
- 成交量: 萎缩

决策评分: +55分
动作: TAKE_PROFIT_NOW（立即止盈）
原因: "RSI极度超买(82) + MACD减弱 + 成交量萎缩 → 提前止盈"
```

### 场景3: MACD死叉 - 立即平仓 ✅
```
标的: 0700.HK (腾讯)
入场价: $350
当前价: $353 (+0.86%)
固定止盈位: $385 (未达到)

技术指标:
- RSI: 48
- MACD: 刚出现死叉（histogram从正转负）
- 价格: 跌破sma_20
- 趋势: 由强转弱

决策评分: +60分
动作: TAKE_PROFIT_NOW（立即止盈）
原因: "MACD死叉 + 跌破均线 → 趋势反转，立即平仓保护利润"
```

### 场景4: 超卖反弹 - 暂缓止损 ✅
```
标的: 1398.HK (工商银行)
入场价: $5.00
当前价: $4.78 (-4.4%)
固定止损位: $4.75 (接近触发)

技术指标:
- RSI: 18 (极度超卖)
- MACD: 负值但柱状图开始收窄（即将金叉）
- 价格: 触及布林带下轨
- 成交量: 放大

决策评分: -35分
动作: DELAY_STOP_LOSS（暂缓止损）
新止损位: $4.65 (-7%)
原因: "RSI极度超卖(18) + 触及BB下轨 + 即将金叉 → 暂缓止损等待反弹"
```

---

## ⚙️ 配置参数

### 可调整参数
```python
class DynamicStopConfig:
    # 评分阈值
    STRONG_HOLD_THRESHOLD = -40        # 强烈持有
    DELAY_TAKE_PROFIT_THRESHOLD = -20  # 延迟止盈
    STANDARD_THRESHOLD_LOW = -10       # 标准下限
    STANDARD_THRESHOLD_HIGH = 10       # 标准上限
    EARLY_TAKE_PROFIT_THRESHOLD = 30   # 提前止盈
    IMMEDIATE_EXIT_THRESHOLD = 50      # 立即平仓

    # 止盈调整
    STANDARD_TAKE_PROFIT_PCT = 0.10    # 10%
    DELAYED_TAKE_PROFIT_PCT = 0.15     # 15%
    STRONG_HOLD_TAKE_PROFIT_PCT = 0.20 # 20%

    # RSI阈值
    RSI_EXTREME_OVERSOLD = 20
    RSI_OVERSOLD = 30
    RSI_STRONG_ZONE_LOW = 50
    RSI_STRONG_ZONE_HIGH = 70
    RSI_OVERBOUGHT = 80

    # 趋势强度
    STRONG_TREND_THRESHOLD = 0.05      # 价格高于SMA20 5%以上
```

---

## 🧪 测试计划

### 回测场景
1. **强趋势延迟止盈**: 测试是否能捕获大涨幅（如15-25%）
2. **顶部反转保护**: 测试是否能在8-10%时提前止盈避免回撤
3. **假突破过滤**: 测试是否能避免被假突破止损
4. **极端波动**: 测试在暴涨暴跌时的表现

### 性能指标
- 平均持有收益: 目标从10%提升至12-15%
- 最大回撤: 控制在5%以内
- 胜率: 保持或提高
- 捕获大行情能力: 提升（15%+涨幅捕获率）

---

## 📝 实现待办

- [ ] 实现 `_fetch_current_indicators()` 方法
- [ ] 实现 `_calculate_exit_score()` 方法
- [ ] 修改 `check_exit_signals()` 集成智能决策
- [ ] 添加配置参数到 settings
- [ ] 编写单元测试
- [ ] 回测验证
- [ ] 文档完善

---

**状态**: 设计完成，准备实现
**预计影响**: 大幅提升系统收益，避免过早止盈和过晚止损
