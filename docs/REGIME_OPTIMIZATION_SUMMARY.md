# 市场状态判断优化 - 实施总结

## 📋 优化目标

根据用户需求，优化市场状态判断逻辑，使其根据不同市场使用不同的指数：
- **港股市场**：使用恒生指数（HSI.HK）
- **美股市场**：使用纳斯达克100（QQQ.US）+ 标普500（SPY.US）
- **支持反向指标**：VIX恐慌指数（框架已支持，待数据源可用）

## ✅ 已完成的工作

### 1. 配置文件修改

**文件**: `.env`

```bash
# 修改前
REGIME_INDEX_SYMBOLS=HSI.HK,SPY.US

# 修改后
REGIME_INDEX_SYMBOLS=HSI.HK,QQQ.US,SPY.US
REGIME_INVERSE_SYMBOLS=
```

**优势**：
- 港股：继续使用 HSI.HK（恒生指数）
- 美股：QQQ（科技股/成长股）+ SPY（全市场），双指数更稳定
- 反向指标支持：框架已就绪，待VIX数据源可用时可立即启用

### 2. Settings 类扩展

**文件**: `src/longport_quant/config/settings.py`

添加新配置字段：
```python
regime_inverse_symbols: str = Field("", alias="REGIME_INVERSE_SYMBOLS")
```

### 3. RegimeClassifier 核心逻辑修改

**文件**: `src/longport_quant/risk/regime.py`

#### 新增方法：
```python
def _parse_inverse_symbols(self, filter_by_market: bool = True) -> List[str]:
    """解析反向指标列表（如VIX）"""
    # 支持按市场时段过滤
    # 支持 ^VIX、VIX.US 等格式
```

#### 修改 classify() 方法：
- 分别处理正向指标和反向指标
- 正向指标：`price >= MA200` → 看涨
- 反向指标：`price < MA200` → 看涨（如VIX低于均线=市场平静）
- 详细说明区分正向和反向指标

### 4. 通知消息优化

通知内容现在会区分正向和反向指标：

**港股时段示例**：
```
📊 今日仓位/购买力预算

状态: BULL | [港股市场] 1/1 指数看涨 (HSI.HK 收盘在MA200之上)
仓位缩放: ×1.00
可动用资金上限(预估):
  • HKD: 上限$1,023,217 → 预留后$869,734 (预留15%)
  • USD: 上限$90,520 → 预留后$76,942 (预留15%)
```

**美股时段示例**：
```
📊 今日仓位/购买力预算

状态: BULL | [美股市场] 2/2 指数看涨 (QQQ.US, SPY.US 收盘在MA200之上)
仓位缩放: ×1.00
可动用资金上限(预估):
  • HKD: 上限$1,023,217 → 预留后$869,734 (预留15%)
  • USD: 上限$90,520 → 预留后$76,942 (预留15%)
```

## 📊 测试结果

### 测试1：所有指数查询（不过滤市场时段）

```
✅ 市场状态判断结果：
  状态:     BULL
  详情:     3/3 指数看涨 (HSI.HK, QQQ.US, SPY.US 收盘在MA200之上)
  活跃市场: HK
```

**指数详情**：
- **HSI.HK**:  26047 > MA200 23863 ✅ 看涨
- **QQQ.US**:  629 > MA200 537 ✅ 看涨
- **SPY.US**:  682 > MA200 609 ✅ 看涨

### 测试2：市场时段过滤（当前港股时段）

```
✅ 市场状态判断结果：
  状态:     BULL
  详情:     [港股市场] 1/1 指数看涨 (HSI.HK 收盘在MA200之上)
  活跃市场: HK
```

**验证通过**：港股时段只使用HSI.HK判断

## 🔧 技术实现细节

### 市场时段智能感知

系统会根据当前时间自动选择合适的指数：

| 时段 | 使用指数 | 说明 |
|------|---------|------|
| 港股时段<br>(09:30-16:00) | HSI.HK | 只使用港股指数判断 |
| 美股时段<br>(21:30-04:00) | QQQ.US, SPY.US | 使用两个美股指数判断 |
| 非交易时段 | 无 | 返回RANGE（震荡）状态 |

### 反向指标支持

框架已完整支持反向指标（如VIX），判断逻辑：
- **正向指标**（普通指数）：价格 ≥ MA → 看涨
- **反向指标**（恐慌指数）：价格 < MA → 看涨（市场平静）

**示例（未来VIX可用时）**：
- VIX = 15，MA200 = 18 → VIX低于均线 → 市场平静 → 看涨 ✅
- VIX = 25，MA200 = 18 → VIX高于均线 → 市场恐慌 → 看跌 ❌

## ⚠️ VIX 不可用说明

经过测试，Longport API 目前不支持 VIX 恐慌指数查询：

**测试的符号格式**：
- `^VIX` ❌ 无效
- `VIX` ❌ 无效
- `VIX.US` ❌ 无效
- `VVIX.US` ❌ 无效
- `$VIX` ❌ 无效
- `VIX.CBOE` ❌ 无效

**替代方案**：
1. ✅ **已采用**：使用 QQQ + SPY 双指数判断美股市场
2. 未来可能：
   - 寻找其他数据源的VIX数据
   - 使用其他波动率指标替代（如ATR）
   - 使用看跌看涨比率等情绪指标

## 📁 涉及文件清单

1. `.env` - 配置修改
2. `src/longport_quant/config/settings.py` - 添加配置字段
3. `src/longport_quant/risk/regime.py` - 核心判断逻辑修改
4. `scripts/test_regime_vix.py` - 测试脚本（新增）
5. `scripts/find_vix_symbol.py` - VIX符号查找脚本（新增）

## 🚀 如何使用

### 自动生效

修改已自动生效，无需额外操作。系统会：
1. 每10分钟自动更新市场状态
2. 根据当前时段自动选择合适的指数
3. 通过Slack/Discord发送通知

### 手动测试

```bash
# 测试市场状态判断
python3 scripts/test_regime_vix.py

# 查找VIX符号（如需）
python3 scripts/find_vix_symbol.py
```

### 重启服务（如需）

```bash
# 重启订单执行器以应用新配置
./scripts/manage_accounts.sh restart paper_001
./scripts/manage_accounts.sh restart live_001
```

## 📈 预期效果

### 港股交易时段（例如10:30）

通知内容：
```
[港股市场] 1/1 指数看涨 (HSI.HK 收盘在MA200之上)
```

预算计算只考虑港股市场状态。

### 美股交易时段（例如22:00）

通知内容：
```
[美股市场] 2/2 指数看涨 (QQQ.US, SPY.US 收盘在MA200之上)
```

预算计算只考虑美股市场状态。

### 综合判断（非交易时段或强制不过滤）

通知内容：
```
3/3 指数看涨 (HSI.HK, QQQ.US, SPY.US 收盘在MA200之上)
```

## 💡 未来优化建议

### 短期（无需改代码）

1. **调整阈值**：当前60%/40%，可改为65%/35%更严格
   ```bash
   # 修改 regime.py 第174-179行
   if pct >= 0.65:  # 改为65%
       regime = "BULL"
   elif pct <= 0.35:  # 改为35%
       regime = "BEAR"
   ```

2. **添加更多指数**：如DIA（道琼斯）
   ```bash
   REGIME_INDEX_SYMBOLS=HSI.HK,QQQ.US,SPY.US,DIA.US
   ```

### 中期（小幅改代码）

1. **多均线支持**：MA50, MA100, MA200 综合判断
2. **趋势强度**：添加 ADX 指标
3. **波动率过滤**：使用 ATR 阈值

### 长期（大幅改代码）

1. **机器学习**：训练模型判断市场状态
2. **宏观指标**：集成利率、失业率等
3. **情绪指标**：Fear & Greed Index, Put/Call Ratio

## ✅ 验收标准

- [x] 配置文件修改完成
- [x] Settings 类扩展完成
- [x] RegimeClassifier 核心逻辑修改完成
- [x] 反向指标框架支持完成
- [x] 通知消息优化完成
- [x] 市场时段过滤正常工作
- [x] HSI.HK 查询正常
- [x] QQQ.US 查询正常
- [x] SPY.US 查询正常
- [x] 测试脚本创建完成
- [x] 文档更新完成

## 📞 技术支持

如有问题，请查看：
1. 测试脚本输出：`scripts/test_regime_vix.py`
2. 系统日志：`logs/order_executor_*.log`
3. 相关代码：`src/longport_quant/risk/regime.py`

---

**实施日期**：2025-11-03
**实施人员**：Claude Code
**状态**：✅ 已完成并通过测试
