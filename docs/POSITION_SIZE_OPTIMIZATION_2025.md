# 仓位计算优化文档 - 2025

## 📋 优化概述

本次优化对量化交易系统的买入仓位计算进行了两个重要改进：
1. **降低最大仓位比例**：从40%降至25%，降低单笔风险暴露
2. **集成Kelly公式**：基于历史胜率和盈亏比动态调整仓位，实现科学化风险管理

**实施日期**: 2025-11-07
**影响范围**: 所有买入信号的仓位计算

---

## 🎯 优化目标

### 问题背景
- **过高的单笔仓位风险**：80-100分信号可达30-40%仓位，单笔最大损失风险过大
- **Kelly公式未使用**：虽然实现了Kelly计算器，但未集成到订单执行流程
- **缺乏历史数据验证**：仓位计算完全基于信号评分，未考虑历史交易表现

### 优化目标
1. **降低风险**：单笔最大损失从40%降至25%，降低37.5%
2. **双重保护**：评分预算 + Kelly公式，取两者较小值
3. **更科学**：基于历史胜率和盈亏比动态调整
4. **更稳健**：提高Kelly启用门槛，确保统计可靠性

---

## 📊 仓位比例调整

### 调整对比表

| 信号评分 | 旧逻辑仓位 | 新逻辑仓位 | 变化幅度 |
|---------|----------|----------|---------|
| **100分** | 40.0% | 25.0% | -37.5% |
| **90分** | 35.0% | 22.5% | -35.7% |
| **80分** | 30.0% | 20.0% | -33.3% |
| **70分** | 25.0% | 18.5% | -26.0% |
| **60分** | 20.0% | 15.0% | -25.0% |
| **55分** | 10.0% | 8.6% | -14.3% |
| **45分** | 5.0% | 5.0% | 0% |

### 公式变更

#### 旧公式（已弃用）
```python
if score >= 80:
    budget_pct = 0.30 + (score - 80) / 200  # 30-40%
elif score >= 60:
    budget_pct = 0.20 + (score - 60) / 200  # 20-30%
elif score >= 45:
    budget_pct = 0.05 + (score - 45) / 200  # 5-12%
else:
    budget_pct = 0.05  # 5%
```

#### 新公式（当前使用）
```python
if score >= 80:
    budget_pct = 0.20 + (score - 80) / 400  # 20-25%
elif score >= 60:
    budget_pct = 0.15 + (score - 60) * 0.07 / 20  # 15-22%
elif score >= 45:
    budget_pct = 0.05 + (score - 45) * 0.05 / 14  # 5-10%
else:
    budget_pct = 0.05  # 5%
```

---

## 🎲 Kelly公式集成

### 集成位置
**文件**: `scripts/order_executor.py`
**方法**: `_calculate_dynamic_budget()`
**行数**: 1944-1970

### 集成逻辑

```python
# 1. 先按信号评分计算基础预算
score_based_budget = net_assets × budget_pct × regime_scale

# 2. 调用Kelly公式计算推荐仓位
kelly_position, kelly_info = await self.kelly_calculator.get_recommended_position(
    total_capital=net_assets,
    signal_score=score,
    symbol=symbol,
    market=market,
    regime=regime
)

# 3. 取两者较小值（双重保险）
final_budget = min(score_based_budget, kelly_position)
```

### Kelly参数优化

| 参数 | 旧值 | 新值 | 说明 |
|-----|------|------|------|
| `KELLY_FRACTION` | 0.5 | **0.4** | 保守系数，降低波动 |
| `KELLY_MAX_POSITION` | 0.25 | **0.20** | 最大仓位，与新上限一致 |
| `KELLY_MIN_WIN_RATE` | 0.55 | **0.60** | 最小胜率，提高门槛 |
| `KELLY_MIN_TRADES` | 10 | **15** | 最少交易数，确保可靠 |

### Kelly公式说明

**完整公式**:
```
f = (p × b - q) / b × kelly_fraction
```

其中：
- `p` = 胜率（win_rate）
- `q` = 1 - p（败率）
- `b` = 平均盈利 / 平均亏损（盈亏比）
- `kelly_fraction` = 保守系数（0.4）

**数据来源**:
- 从PostgreSQL `position_stops` 表读取最近30天交易记录
- 统计个股、市场、全市场三个层级的胜率和盈亏比
- 优先使用个股数据，回退到市场数据，最后使用全市场数据

---

## 💡 实际案例对比

### 案例1: NVDA $140（80分信号，牛市，净资产$50,000）

| 步骤 | 旧逻辑 | 新逻辑 | 变化 |
|-----|--------|--------|------|
| **基础预算** | $15,000 (30%) | $10,000 (20%) | -33% |
| **Regime调整** | $15,000 (×1.0) | $10,000 (×1.0) | -33% |
| **Kelly验证** | 未启用 | $8,500 (17%) | **-43%** |
| **最终预算** | $15,000 | $8,500 | -43% |
| **买入数量** | 107股 | 60股 | -44% |
| **实际成本** | $14,980 | $8,400 | -44% |
| **占净资产** | 30.0% | 16.8% | -44% |

### 案例2: AAPL $270（60分信号，震荡，净资产$50,000）

| 步骤 | 旧逻辑 | 新逻辑 | 变化 |
|-----|--------|--------|------|
| **基础预算** | $10,000 (20%) | $7,500 (15%) | -25% |
| **Regime调整** | $7,000 (×0.7) | $5,250 (×0.7) | -25% |
| **Kelly验证** | 未启用 | $6,000 (12%) | - |
| **最终预算** | $7,000 | $5,250 | **-25%** |
| **买入数量** | 25股 | 19股 | -24% |
| **实际成本** | $6,750 | $5,130 | -24% |
| **占净资产** | 13.5% | 10.3% | -24% |

---

## 🔧 技术实现细节

### 文件修改清单

#### 1. `scripts/order_executor.py`
**修改内容**:
- 导入 `KellyCalculator`（第40行）
- 初始化 `kelly_calculator`（第107行）
- 修改 `max_position_size_pct` 从 0.40 → 0.25（第88行）
- 降低仓位比例计算公式（第1837-1848行）
- 将 `_calculate_dynamic_budget()` 改为 async（第1818行）
- 集成 Kelly 公式到预算计算（第1944-1970行）
- 更新所有调用点添加 await（第579, 890, 967行）

#### 2. `.env`
**修改内容**:
```bash
# Kelly 公式优化
KELLY_FRACTION=0.4              # 从0.5降低到0.4
KELLY_MAX_POSITION=0.20         # 从0.25降低到0.20
KELLY_MIN_WIN_RATE=0.60         # 从0.55提高到0.60
KELLY_MIN_TRADES=15             # 从10提高到15
```

### 关键代码片段

#### Kelly 集成代码
```python
# 🎲 集成 Kelly 公式：基于历史胜率和盈亏比动态调整仓位
try:
    market = "HK" if ".HK" in symbol else ("US" if ".US" in symbol else None)
    kelly_position, kelly_info = await self.kelly_calculator.get_recommended_position(
        total_capital=net_assets,
        signal_score=score,
        symbol=symbol,
        market=market,
        regime=regime
    )

    # 取评分预算和 Kelly 推荐的较小值（双重保险）
    if kelly_position > 0 and kelly_position < dynamic_budget:
        logger.info(
            f"  🎲 Kelly 保护: 评分预算=${dynamic_budget:,.2f}, "
            f"Kelly推荐=${kelly_position:,.2f} (胜率={kelly_info.get('win_rate', 0):.1%}, "
            f"盈亏比={kelly_info.get('profit_loss_ratio', 0):.2f}), "
            f"采用较小值"
        )
        dynamic_budget = kelly_position
    elif kelly_position > 0:
        logger.debug(
            f"  ℹ️ Kelly推荐=${kelly_position:,.2f} ≥ 评分预算=${dynamic_budget:,.2f}, "
            f"保持评分预算"
        )
except Exception as e:
    logger.debug(f"Kelly公式计算失败（忽略）: {e}")
```

---

## 📈 预期效果

### 风险控制
- ✅ **单笔最大风险降低37.5%**：从40%降至25%
- ✅ **平均仓位降低25-35%**：根据信号评分不同而异
- ✅ **Kelly双重保护**：历史表现差的标的自动降低仓位

### 资金利用
- ⚠️ **满仓难度增加**：需要更多高分信号才能满仓
- ✅ **更多分散机会**：单笔仓位降低，可同时持有更多标的
- ✅ **预留充足现金**：应对市场突发机会

### 收益预期
- ✅ **风险调整后收益提升**：降低回撤，提高夏普比率
- ⚠️ **绝对收益可能降低**：单笔盈利空间缩小
- ✅ **长期稳定性增强**：降低极端损失风险

---

## 🧪 测试验证

### 测试脚本
**文件**: `test_position_calculation_optimization.py`

**运行方法**:
```bash
python3 test_position_calculation_optimization.py
```

**测试内容**:
1. 仓位比例对比表（旧逻辑 vs 新逻辑）
2. 实际买入案例测试（7个典型场景）
3. 不同评分、不同市场状态的组合测试

### 测试结果
✅ 所有测试通过
✅ 代码无语法错误
✅ 仓位计算符合预期

---

## 🔍 监控指标

### 关键日志
在 `logs/order_executor_*.log` 中查找以下关键词：

```bash
# Kelly 保护生效
grep "🎲 Kelly 保护" logs/order_executor_*.log

# 预算计算详情
grep "动态预算计算" logs/order_executor_*.log

# Regime 调整
grep "Regime仓位缩放" logs/order_executor_*.log
```

### 监控指标
1. **仓位分布**：统计实际买入仓位占比的分布
2. **Kelly触发率**：Kelly保护生效的比例
3. **胜率达标率**：符合Kelly启用条件（≥60%胜率）的交易占比
4. **平均仓位**：对比优化前后的平均买入仓位

---

## ⚙️ 配置调整建议

### 如果觉得仓位太小
```bash
# 方案1: 提高评分预算（不推荐，破坏优化效果）
# 不建议修改 order_executor.py 中的公式

# 方案2: 降低 Kelly 保守系数（适度调整）
KELLY_FRACTION=0.5  # 从0.4提高到0.5

# 方案3: 提高信号评分（推荐）
# 优化信号生成逻辑，让更多信号达到70-80分
```

### 如果觉得仓位太大
```bash
# 方案1: 进一步降低最大仓位
# 修改 order_executor.py 第88行
self.max_position_size_pct = 0.20  # 从0.25降到0.20

# 方案2: 进一步降低 Kelly 保守系数
KELLY_FRACTION=0.3  # 从0.4降到0.3

# 方案3: 提高 Kelly 门槛
KELLY_MIN_WIN_RATE=0.65  # 从0.60提高到0.65
KELLY_MIN_TRADES=20       # 从15提高到20
```

---

## 📚 相关文档

- **Kelly公式实现**: `src/longport_quant/risk/kelly.py`
- **订单执行器**: `scripts/order_executor.py`
- **配置说明**: `.env`
- **测试脚本**: `test_position_calculation_optimization.py`

---

## 🔄 回滚方案

如果需要回滚到旧逻辑：

### 1. 修改 `order_executor.py`
```python
# 第88行
self.max_position_size_pct = 0.40  # 改回0.40

# 第1837-1848行（恢复旧公式）
if score >= 80:
    budget_pct = 0.30 + (score - 80) / 200
elif score >= 60:
    budget_pct = 0.20 + (score - 60) / 200
elif score >= 45:
    budget_pct = 0.05 + (score - 45) / 200
else:
    budget_pct = 0.05

# 删除或注释第1944-1970行的 Kelly 集成代码
```

### 2. 修改 `.env`
```bash
# 恢复旧 Kelly 参数
KELLY_FRACTION=0.5
KELLY_MAX_POSITION=0.25
KELLY_MIN_WIN_RATE=0.55
KELLY_MIN_TRADES=10

# 或者直接禁用 Kelly
KELLY_ENABLED=false
```

---

## ✅ 总结

本次优化通过**降低最大仓位**和**集成Kelly公式**，实现了：

1. **风险降低**：单笔最大损失从40%降至25%（-37.5%）
2. **双重保护**：评分预算 + Kelly验证，取较小值
3. **更科学**：基于历史胜率和盈亏比动态调整
4. **更稳健**：提高Kelly启用门槛，确保统计可靠性

预期效果：
- ✅ 降低回撤幅度
- ✅ 提高风险调整后收益（夏普比率）
- ✅ 增强长期稳定性
- ⚠️ 短期绝对收益可能降低

**建议观察1-2周，根据实际效果调整参数。**
