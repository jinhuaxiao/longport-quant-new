# 港股买入力问题 - 快速修复指南

## 问题症状
```
❌ 日志显示：
   • HKD现金: $690,292.00 ✅ 有现金
   • HKD购买力: -$50,000.00 ❌ 买入力为负
   • 结果: 订单被拒绝，提示"可买数量为0"
```

## 根本原因
```
跨币种融资债务导致：
├─ USD账户可能欠债（-$50,000）
├─ 拖累HKD的购买力计算
└─ LongPort API保守地返回可买数量=0
```

## 立即修复（代码实施）

### 修复1：Fallback估算（优先级最高）
**文件**：`/data/web/longport-quant-new/scripts/order_executor.py`
**行号**：2082-2126，方法`_estimate_available_quantity`

**改动内容**：
```python
# 原代码（有问题）
async def _estimate_available_quantity(self, symbol, price, lot_size, currency):
    try:
        estimate = await self.trade_client.estimate_max_purchase_quantity(...)
        # ... 取max_qty ...
        lots = int(max_qty // lot_size)
        return lots * lot_size if lots > 0 else 0
    except Exception as e:
        logger.debug(f"⚠️ 预估最大可买数量失败: {e}")
        return 0

# 修复后（新增fallback）
async def _estimate_available_quantity(self, symbol, price, lot_size, currency):
    try:
        estimate = await self.trade_client.estimate_max_purchase_quantity(...)
        candidates = []
        if getattr(estimate, "margin_max_qty", None):
            candidates.append(float(estimate.margin_max_qty))
        if getattr(estimate, "cash_max_qty", None):
            candidates.append(float(estimate.cash_max_qty))
        
        if candidates and max(candidates) > 0:
            max_qty = max(candidates)
            lots = int(max_qty // lot_size)
            if lots > 0:
                return lots * lot_size
        
        # 🔥 NEW: Fallback当API返回0
        logger.debug(f"⚠️ API估算返回0，尝试现金fallback...")
        try:
            account = await self.trade_client.get_account()
            available_cash = account.get("cash", {}).get(currency, 0)
            
            if available_cash > price * lot_size * 1.5:  # 保留1.5倍手数的安全边际
                conservative_qty = int((available_cash * 0.5) / price) // lot_size * lot_size
                if conservative_qty > 0:
                    logger.warning(
                        f"  ⚠️ buy_power={account.get('buy_power', {}).get(currency, 0):.0f}(负值或不足), "
                        f"改用现金fallback: 可买{conservative_qty}股"
                    )
                    return conservative_qty
        except Exception as fallback_err:
            logger.debug(f"  Fallback失败: {fallback_err}")
        
        return 0
        
    except Exception as e:
        logger.debug(f"  ⚠️ 预估最大可买数量失败: {e}")
        return 0
```

**验证**：修改后，当buy_power<0时会自动用50%的可用现金进行估算

---

### 修复2：诊断信息（帮助排查）
**文件**：`/data/web/longport-quant-new/scripts/order_executor.py`
**行号**：1022-1032，方法`execute_order`

**在资金检查部分添加**：
```python
# 原位置（第1022-1032行）
available_cash = account["cash"].get(currency, 0)
buy_power = account.get("buy_power", {}).get(currency, 0)
remaining_finance = account.get("remaining_finance", {}).get(currency, 0)

logger.debug(...)

# 🔥 NEW：添加跨币种诊断
if available_cash > 0 and buy_power < 0:
    other_ccy = "USD" if currency == "HKD" else "HKD"
    other_cash = account.get("cash", {}).get(other_ccy, 0)
    other_bp = account.get("buy_power", {}).get(other_ccy, 0)
    logger.warning(
        f"  🔥 跨币种债务影响检测:\n"
        f"     • {currency}: 现金=${available_cash:,.0f}, 购买力=${buy_power:,.0f}\n"
        f"     • {other_ccy}: 现金=${other_cash:,.0f}, 购买力=${other_bp:,.0f}\n"
        f"     • 原因: 可能{other_ccy}账户出现负债或融资占用\n"
        f"     • 解决: Fallback使用{currency}现金下单，或检查{other_ccy}持仓"
    )
```

---

### 修复3：Rebalancer增强（防止复发）
**文件**：`/data/web/longport-quant-new/src/longport_quant/risk/rebalancer.py`
**行号**：103-131，方法`run_once`

**在计算目标持仓前添加**：
```python
for ccy, items in by_currency.items():
    equity = float(account.get("net_assets", {}).get(ccy, 0) or 0)
    if equity <= 0:
        continue
    
    # 🔥 NEW：检查买入力
    buy_power = float(account.get("buy_power", {}).get(ccy, 0) or 0)
    if buy_power < 0:
        logger.warning(
            f"{ccy}: 购买力为负(${buy_power:,.0f}), "
            f"强制增加预留比例以释放购买力"
        )
        reserve = min(reserve + 0.20, 0.80)  # 增加预留20%，最多80%
    
    # 计算目标持仓
    total_value = 0.0
    # ... 继续原有逻辑 ...
    
    target_value = equity * (1.0 - reserve)
```

---

## 快速验证清单

- [ ] 修改文件1：order_executor.py 中的 `_estimate_available_quantity` 方法
- [ ] 修改文件2：order_executor.py 中的 `execute_order` 方法
- [ ] 修改文件3：rebalancer.py 中的 `run_once` 方法
- [ ] 重启订单执行器
- [ ] 观察日志是否出现fallback消息
- [ ] 重新测试买入信号

## 预期结果

修复前：
```
❌ 预估最大可买数量为0
   原因：购买力=${buy_power} < 0
```

修复后：
```
✅ buy_power={buy_power}(负值或不足), 改用现金fallback: 可买{数量}股
   成功下单！
```

## 额外说明

### 为什么这样修复安全?
1. **Fallback只在必要时触发** - API返回0时才执行
2. **使用保守估计** - 只用50%可用现金，留50%安全边际
3. **没有绕过风控** - 只是改变估算方法，不改变下单逻辑
4. **可自动降级** - 若现金不足仍会返回0，维持原有拒绝

### 为什么会有跨币种债务?
常见原因：
- USD头寸亏损，产生USD欠债
- 融资额度被分配给多币种使用
- 汇率变化影响保证金率计算

### 长期解决方案
1. 定期检查所有币种的现金状况
2. 不要同时在多币种做融资交易
3. 或者要求LongPort提高融资额度

