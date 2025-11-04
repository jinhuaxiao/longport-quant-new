# 港股账户买入力(Buy Power)深度分析报告

## 核心问题诊断

用户遇到的问题现象：
- 港股账户显示有可用现金（HKD现金 > 0）
- 但Longport API返回的`buy_power`为负数
- 导致订单执行器拒绝下单

---

## 1. 买入力(Buy Power)计算的完整逻辑

### 1.1 数据获取源头 - `client.py`中的`get_account()`方法

**位置**: `/data/web/longport-quant-new/src/longport_quant/execution/client.py` (第548-671行)

关键代码段：
```python
async def get_account(self) -> Dict[str, Any]:
    """获取账户信息（包括cash、buy_power、positions等）"""
    balances = await self.account_balance()  # 调用LongPort SDK
    positions_resp = await self.stock_positions()
    
    # 为每个币种获取详细信息
    for currency in all_currencies:
        balance = currency_balance[0]
        
        # 获取购买力（仅供参考，不作为下单依据）
        buy_power[currency] = float(balance.buy_power)
        
        # 获取现金信息
        actual_cash = float(cash_info.available_cash)
        withdraw_cash_amount = float(cash_info.withdraw_cash)
        frozen_cash_amount = float(cash_info.frozen_cash)
```

### 1.2 现金vs购买力的策略选择逻辑

**关键逻辑（第610-636行）**：

```python
# 1. 如果 available_cash 为负数（使用了融资），则使用 buy_power
# 2. 否则使用 available_cash（已扣除冻结资金）
if actual_cash < 0:
    # 账户使用了融资，现金为负数，使用购买力
    cash[currency] = buy_power[currency]
    logger.debug(
        f"{currency} 账户使用融资: "
        f"欠款=${actual_cash:,.2f}, "
        f"购买力=${buy_power[currency]:,.2f}"
    )
else:
    # 正常情况，使用可用现金
    cash[currency] = actual_cash
```

**结果返回**：
```python
return {
    "account_id": "",
    "cash": cash,              # 选定的可用资金
    "buy_power": buy_power,    # 原始购买力（可能为负）
    "net_assets": net_assets,  # 净资产
    "remaining_finance": remaining_finance,  # 剩余融资额度
    "positions": positions,
    "position_count": len(positions)
}
```

---

## 2. 现金(Cash) vs 购买力(Buy Power)的区别

### 2.1 现金(Cash)是什么?

**定义**：账户中实际可用于交易的现金金额

**构成**：
- `available_cash` = 账户现金 - 冻结资金（挂单占用）
- 可能为负数（表示使用了融资）

**特点**：
- 是账户真实的现金存量
- 可以为负数（融资时）
- 不受融资额度的限制

### 2.2 购买力(Buy Power)是什么?

**定义**：经纪商(LongPort)计算的当前可以用于交易的最大购买力度

**公式**（LongPort的逻辑）：
```
buy_power = (现有现金 + 融资额度) * (1 - 维持保证金率) - 已用融资额度
```

**可能为负的原因**：
```
场景1：跨币种融资债务
  HKD: available_cash = 500,000（有充足现金）
  USD: available_cash = -50,000（欠USD）
  
  当USD欠债时，根据LongPort的风险模型：
  - USD的buy_power = 实际经纪商计算（考虑汇率折合）
  - 如果USD债务+HKD已用融资 > 总融资额度
  - → HKD的buy_power可能为负（表示HKD债务）

场景2：维持保证金不足
  已有融资 1,000,000 HKD
  持仓下跌，保证金率从150% → 120%（低于140%维持线）
  → buy_power变为负数（强制清仓预警）
```

### 2.3 图表对比

```
┌─────────────────────────────────────────────────────────────────────┐
│                   HKD账户现金状态变化过程                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  账户初始状态：                                                        │
│  ├─ 现金(cash)            = 100,000 HKD ✅                          │
│  ├─ 购买力(buy_power)     = 100,000 HKD ✅                          │
│  └─ 剩余融资额度(remain)  = 500,000 HKD ✅                          │
│                                                                       │
│  场景A：使用融资买入                                                   │
│  ├─ 现金(cash)            = 100,000 HKD ✅                          │
│  ├─ 购买力(buy_power)     = 600,000 HKD ✅（现金+融资）              │
│  └─ 剩余融资额度          = 500,000 HKD ✅                          │
│                                                                       │
│  场景B：持仓下跌，USD欠债                                              │
│  ├─ HKD现金              = 100,000 HKD ✅                          │
│  ├─ USD欠债              = -50,000 USD ❌                          │
│  ├─ HKD购买力            = -150,000 HKD ❌ (跨币种债务拖累)         │
│  └─ 剩余融资额度          = 350,000 HKD ⚠️ (部分用于覆盖USD债)     │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 为什么有现金但买入力为负?

### 3.1 根本原因：跨币种融资债务

**LongPort的风险管理策略**：
1. 账户级别维持总融资余额和保证金率
2. 当出现多币种时，计算跨币种净头寸
3. 如果USD欠债，会拖累其他币种（如HKD）的买入力

**具体场景**：
```
假设账户情况：
├─ HKD: 现金 100,000, 持仓市值 500,000
├─ USD: 现金 -50,000（欠债）, 持仓市值 300,000
├─ 总融资额度: 500,000 HKD等值
├─ 已用融资: 300,000 HKD等值
├─ 剩余融资: 200,000 HKD等值

LongPort计算：
1. 计算账户总净资产（USD转HKD）
   = HKD现金(100,000) + HKD持仓(500,000) 
     + USD现金(-50,000) + USD持仓(300,000)  [按汇率转换]
   
2. 计算总融资使用比例
   = 已用融资 / 融资额度
   = 300,000 / 500,000 = 60% ✅ 安全

3. 但LongPort可能在币种级别设置了风险限制：
   - 若HKD维持保证金率 = 140%（行业标准）
   - 若USD维持保证金率 = 140%
   
4. 当USD欠债时，部分HKD融资额度被占用来覆盖USD债务
   → HKD实际可用融资额度 < 200,000
   → HKD的buy_power可能降为负数
```

### 3.2 代码中的处理逻辑

**在order_executor.py中**（第1921-1989行）：

```python
def _calculate_dynamic_budget(self, account: Dict, signal: Dict) -> float:
    available_cash = account.get("cash", {}).get(currency, 0)
    remaining_finance = account.get("remaining_finance", {}).get(currency, 0)
    buy_power = account.get("buy_power", {}).get(currency, 0)
    
    # 计算可支配上限：优先使用购买力，其次可用资金，最后剩余融资额度
    if buy_power and buy_power > 0:
        effective_cap = buy_power  # ✅ 优先用buy_power
    else:
        effective_cap = max(available_cash, 0.0)  # ⚠️ buy_power≤0时，降级用现金
        if effective_cap <= 0 and remaining_finance > 0:
            effective_cap = remaining_finance  # 最后才用融资额度
```

**问题在这里**：当buy_power为负时，代码会依次尝试：
1. 使用buy_power → 失败（为负）
2. 使用available_cash → 如果可用，成功！
3. 使用remaining_finance → 兜底方案

---

## 4. 订单执行中的买入力检查

### 4.1 估算最大可买数量的逻辑

**位置**：`order_executor.py`（第2082-2126行）

```python
async def _estimate_available_quantity(self, symbol, price, lot_size, currency):
    """调用交易端口预估最大可买数量（含融资）"""
    estimate = await self.trade_client.estimate_max_purchase_quantity(
        symbol=symbol,
        order_type=openapi.OrderType.Limit,
        side=openapi.OrderSide.Buy,
        price=price,
        currency=currency  # ⚠️ 按币种分别估算
    )
    
    # LongPort API返回两个值
    candidates = []
    if estimate.margin_max_qty:      # 使用融资时的最大购买量
        candidates.append(estimate.margin_max_qty)
    if estimate.cash_max_qty:        # 使用现金时的最大购买量
        candidates.append(estimate.cash_max_qty)
    
    # 取两者中较大值
    max_qty = max(candidates)
    
    # 按手数取整
    lots = int(max_qty // lot_size)
    return lots * lot_size if lots > 0 else 0
```

### 4.2 关键问题：返回0的条件

当估算可买数量为0时，会触发以下流程（第1117-1247行）：

```python
if estimated_quantity > 0:
    # ✅ 有可买数量，继续
else:
    # ❌ 可买数量为0
    logger.warning(f"⚠️ {symbol}: 预估最大可买数量为0")
    
    # 尝试挪仓策略（基于信号评分）
    if score >= 70:
        rotation_success = await self._try_smart_rotation(
            signal, needed_amount, score_threshold=10
        )
    elif score >= 55:
        rotation_success = await self._try_smart_rotation(...)
    else:
        # 评分太低，不尝试挪仓，直接返回（不下单）
        if self.slack:
            await self._send_capacity_notification(
                reason=f"预估数量为0且评分{score}分太低不触发挪仓"
            )
        return  # ❌ 退出，信号被标记为完成但没有下单
```

**根本问题**：
- `estimate_max_purchase_quantity`返回0
- 可能原因：LongPort API在币种级别检查了buy_power
- 即使HKD有现金，但如果buy_power < 0，API仍返回0

---

## 5. Rebalancer的减仓逻辑

### 5.1 何时触发减仓

**位置**：`rebalancer.py`（第47-127行）

```python
# 计算目标持仓市值（预留现金reserve%）
target_value = equity * (1.0 - reserve)

if total_value <= target_value:
    logger.info(f"{ccy}: 当前持仓${total_value:,.0f} ≤ 目标${target_value:,.0f}，无需减仓")
    continue  # ⚠️ 跳过，不减仓
```

**从日志看**：
```
HKD: 当前持仓$690,292 ≤ 目标$1,105,409，无需减仓
```

这说明：
- 当前持仓 690,292 < 目标持仓 1,105,409
- Regime为RANGE，预留30%现金
- 所以不触发减仓

**问题**：
- 减仓逻辑**只看持仓市值和目标仓位**
- **不考虑当前buy_power是否为负**
- 应该增加：当buy_power < 0时，强制减仓释放购买力

### 5.2 改进建议

```python
# 在rebalancer.py中添加买入力检查
buy_power = account.get("buy_power", {}).get(ccy, 0)
if buy_power < 0:
    logger.warning(f"{ccy}: 购买力为负({buy_power}), 强制减仓释放资金")
    # 强制减仓目标：target_value = equity * (1.0 - 0.5)  # 增加预留比例
    target_value = equity * 0.5  # 50%预留
```

---

## 6. 根本修复方案

### 方案1: 在estimate_max_purchase_quantity失败时的备选方案（推荐）

**修改位置**：`order_executor.py`的`_estimate_available_quantity()`

```python
async def _estimate_available_quantity(self, symbol, price, lot_size, currency):
    """改进版：当estimate失败时，采用fallback方案"""
    
    try:
        estimate = await self.trade_client.estimate_max_purchase_quantity(...)
        
        candidates = []
        if estimate.margin_max_qty:
            candidates.append(float(estimate.margin_max_qty))
        if estimate.cash_max_qty:
            candidates.append(float(estimate.cash_max_qty))
        
        if candidates:
            max_qty = max(candidates)
            if max_qty > 0:
                lots = int(max_qty // lot_size)
                return lots * lot_size
        
        # 🔥 Fallback: 当API返回0时，使用available_cash估算
        # 这是因为：即使buy_power为负（跨币种债务），
        # 但available_cash为正时仍可以下单
        available_cash = (await self.trade_client.get_account()).get("cash", {}).get(currency, 0)
        if available_cash > price * lot_size:
            # 保守估计：只用50%可用现金
            conservative_qty = int((available_cash * 0.5) / price) // lot_size * lot_size
            if conservative_qty > 0:
                logger.warning(
                    f"  ⚠️ API估算失败(buy_power可能为负), "
                    f"fallback到现金估算: {conservative_qty}股"
                )
                return conservative_qty
        
        return 0
        
    except Exception as e:
        logger.debug(f"  ⚠️ 预估最大可买数量失败: {e}")
        return 0
```

### 方案2: 检查并输出跨币种债务信息（诊断）

**修改位置**：`order_executor.py`的`execute_order()`

```python
async def execute_order(self, signal: Dict):
    # ... 现有代码 ...
    
    # 添加：诊断跨币种问题
    account = await self.trade_client.get_account()
    for ccy in ['HKD', 'USD']:
        cash = account.get("cash", {}).get(ccy, 0)
        bp = account.get("buy_power", {}).get(ccy, 0)
        if cash > 0 and bp < 0:
            logger.warning(
                f"  🔥 诊断: {ccy}出现跨币种债务影响\n"
                f"     • 现金: ${cash:,.2f} ✅\n"
                f"     • 购买力: ${bp:,.2f} ❌\n"
                f"     • 原因: 其他币种可能有债务或融资占用\n"
                f"     • 建议: 检查账户其他币种的头寸"
            )
```

### 方案3: 在Rebalancer中添加买入力监控

**修改位置**：`rebalancer.py`的`run_once()`

```python
# 在计算目标持仓之前，检查buy_power
buy_power = float(account.get("buy_power", {}).get(ccy, 0) or 0)
net_assets = float(account.get("net_assets", {}).get(ccy, 0) or 0)

# 🔥 新增：当buy_power为负时，强制增加预留比例
if buy_power < 0:
    logger.warning(
        f"{ccy}: 购买力为负(${buy_power:,.2f})，强制减仓\n"
        f"     • 净资产: ${net_assets:,.2f}\n"
        f"     • 原因: 可能存在跨币种债务\n"
        f"     • 策略: 将预留比例从{reserve*100:.0f}% → 60%"
    )
    reserve = 0.60  # 强制60%预留，只保留40%投资仓位

# 继续原有逻辑...
target_value = equity * (1.0 - reserve)
if total_value > target_value:
    # 触发减仓
```

### 方案4: 修改下单逻辑的优先级

**关键修改**：改变对现金vs买入力的判断逻辑

```python
# 原逻辑（有问题）：
if buy_power > 0:
    effective_cap = buy_power
else:
    effective_cap = available_cash

# 改进逻辑：
# 改为：只要有可用现金，就允许下单
# 买入力为负可能是跨币种问题，不应该完全否决
if available_cash > min_required_cash:
    # ✅ 有足够现金，使用现金作为上限
    effective_cap = available_cash
elif buy_power > 0:
    # ✅ 买入力为正，使用买入力
    effective_cap = buy_power
else:
    # ❌ 都不够，无法交易
    effective_cap = 0
```

---

## 7. 问题根源总结

| 问题 | 原因 | 症状 | 修复 |
|------|------|------|------|
| 买入力为负 | 跨币种融资债务 | HKD有现金但buy_power<0 | 检查所有币种 |
| estimate返回0 | API基于buy_power检查 | 无法估算可买数量 | Fallback到现金 |
| 订单被拒绝 | 优先级错误 | 有现金却不下单 | 改变优先级 |
| 不减仓 | 持仓未超目标 | 持仓没释放 | 买入力<0时强制减仓 |

---

## 8. 实施步骤（优先级）

### 优先级1（立即修复）：Fallback方案
- 修改`_estimate_available_quantity()`
- 当API返回0时，改用可用现金估算
- 风险低，可立即上线

### 优先级2（诊断）：跨币种债务检测
- 在execute_order中添加诊断日志
- 帮助用户理解问题根源
- 无风险，有助于故障排查

### 优先级3（完整修复）：重构优先级逻辑
- 改变现金vs买入力的判断顺序
- 测试所有币种组合
- 需要较多测试

### 优先级4（预防）：Rebalancer增强
- 添加买入力<0时强制减仓
- 配合优先级1使用
- 防止未来再出现同样问题

---

## 9. 代码位置快速查询

| 功能 | 文件 | 行号 | 说明 |
|------|------|------|------|
| 获取账户信息 | `client.py` | 548-671 | get_account方法 |
| 现金优先级 | `client.py` | 610-636 | 负数处理逻辑 |
| 动态预算 | `order_executor.py` | 1921-1989 | _calculate_dynamic_budget |
| 估算可买量 | `order_executor.py` | 2082-2126 | _estimate_available_quantity |
| 订单执行 | `order_executor.py` | 1017-1248 | execute_order中的资金检查 |
| 减仓逻辑 | `rebalancer.py` | 47-127 | 目标持仓计算 |

---

## 10. 测试案例

### 测试场景1：正常情况
```
HKD: 现金=100,000, 购买力=100,000 ✅
USD: 现金=50,000, 购买力=50,000 ✅
结果: 应下单成功
```

### 测试场景2：跨币种债务（关键）
```
HKD: 现金=100,000, 购买力=-50,000 ❌（USD债务）
USD: 现金=-50,000, 购买力=0
结果: 应改用HKD现金下单，而非拒绝
```

### 测试场景3：融资账户
```
HKD: 现金=50,000, 购买力=500,000（含融资）
USD: 现金=0, 购买力=0
结果: 应使用购买力下单
```

---

## 11. 关键结论

1. **buy_power为负 ≠ 无法交易**
   - 只表示跨币种头寸出现风险
   - 但各币种可能有独立的现金

2. **现金才是交易的基础**
   - 如果HKD现金>0，理论上可以用HKD交易
   - buy_power主要是经纪商的风险管理指标

3. **LongPort API的estimate可能过于保守**
   - 当buy_power<0时，可能返回0
   - 即使实际有可用现金

4. **需要分层的资金管理策略**
   - 第1层：优先用buy_power（最安全）
   - 第2层：用可用现金（有风险但可行）
   - 第3层：使用融资额度（需谨慎）

