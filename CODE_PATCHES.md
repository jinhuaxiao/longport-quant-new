# 港股买入力问题 - 代码补丁详细说明

## 补丁1：修复_estimate_available_quantity方法

### 文件位置
`/data/web/longport-quant-new/scripts/order_executor.py`
第2082-2126行

### 原始代码
```python
async def _estimate_available_quantity(
    self,
    symbol: str,
    price: float,
    lot_size: int,
    currency: str
) -> int:
    """
    调用交易端口预估最大可买数量（含融资），并按手数取整。

    Returns:
        int: 按手数取整后的最大可买数量，若不可用返回0
    """
    try:
        estimate = await self.trade_client.estimate_max_purchase_quantity(
            symbol=symbol,
            order_type=openapi.OrderType.Limit,
            side=openapi.OrderSide.Buy,
            price=price,
            currency=currency
        )

        candidates = []
        if getattr(estimate, "margin_max_qty", None):
            candidates.append(float(estimate.margin_max_qty))
        if getattr(estimate, "cash_max_qty", None):
            candidates.append(float(estimate.cash_max_qty))

        if not candidates:
            return 0

        max_qty = max(candidates)
        if max_qty <= 0:
            return 0

        lots = int(max_qty // lot_size)
        if lots <= 0:
            return 0

        return lots * lot_size

    except Exception as e:
        logger.debug(f"  ⚠️ 预估最大可买数量失败: {e}")
        return 0
```

### 修复后代码
```python
async def _estimate_available_quantity(
    self,
    symbol: str,
    price: float,
    lot_size: int,
    currency: str
) -> int:
    """
    调用交易端口预估最大可买数量（含融资），并按手数取整。
    当API失败时，自动fallback到现金估算（用于处理跨币种债务导致buy_power为负的情况）

    Returns:
        int: 按手数取整后的最大可买数量，若不可用返回0
    """
    try:
        estimate = await self.trade_client.estimate_max_purchase_quantity(
            symbol=symbol,
            order_type=openapi.OrderType.Limit,
            side=openapi.OrderSide.Buy,
            price=price,
            currency=currency
        )

        candidates = []
        if getattr(estimate, "margin_max_qty", None):
            candidates.append(float(estimate.margin_max_qty))
        if getattr(estimate, "cash_max_qty", None):
            candidates.append(float(estimate.cash_max_qty))

        if candidates:
            max_qty = max(candidates)
            if max_qty > 0:
                lots = int(max_qty // lot_size)
                if lots > 0:
                    return lots * lot_size
        
        # 🔥 NEW: Fallback当API返回0（可能由于buy_power为负）
        logger.debug(f"  ⚠️ {symbol} {currency}估算返回0，尝试现金fallback...")
        try:
            account = await self.trade_client.get_account()
            available_cash = account.get("cash", {}).get(currency, 0)
            buy_power = account.get("buy_power", {}).get(currency, 0)
            
            # 只有在有充足现金时才fallback
            min_required = price * lot_size * 1.5  # 1.5手的保险边际
            if available_cash > min_required:
                # 保守策略：只用50%现金，留50%安全边际
                conservative_qty = int((available_cash * 0.5) / price) // lot_size * lot_size
                if conservative_qty > 0:
                    logger.warning(
                        f"  ⚠️ {symbol} {currency}估算失败(buy_power=${buy_power:,.0f}), "
                        f"现金fallback: ${available_cash:,.0f} → 可买{conservative_qty}股"
                    )
                    return conservative_qty
            else:
                logger.debug(
                    f"  📊 {symbol} {currency}现金不足fallback "
                    f"(需${min_required:,.0f}, 有${available_cash:,.0f})"
                )
        except Exception as fallback_err:
            logger.debug(f"  Fallback异常: {fallback_err}")
        
        return 0

    except Exception as e:
        logger.debug(f"  ⚠️ 预估最大可买数量失败: {e}")
        return 0
```

### 改动说明
1. **新增Fallback逻辑**：当API返回0时，检查可用现金
2. **保守估算**：只用50%现金，保留50%安全边际
3. **安全检查**：要求至少有1.5手的现金才触发fallback
4. **详细日志**：记录fallback的原因和估算结果

### 影响范围
- 调用者：`execute_order()`方法中第1096-1101行和第1189-1194行

---

## 补丁2：添加跨币种债务诊断

### 文件位置
`/data/web/longport-quant-new/scripts/order_executor.py`
第1022-1032行（资金检查部分）

### 原始代码
```python
# 3. 资金检查
currency = "HKD" if ".HK" in symbol else "USD"
available_cash = account["cash"].get(currency, 0)
buy_power = account.get("buy_power", {}).get(currency, 0)
remaining_finance = account.get("remaining_finance", {}).get(currency, 0)

# 显示购买力和融资额度信息
logger.debug(
    f"  💰 {currency} 资金状态 - 可用: ${available_cash:,.2f}, "
    f"购买力: ${buy_power:,.2f}, 剩余融资额度: ${remaining_finance:,.2f}"
)

if available_cash < 0:
    logger.error(
        f"  ❌ {symbol}: 资金异常（显示为负数: ${available_cash:.2f}）\n"
        f"     可能原因：融资账户或数据错误"
    )
    if account.get('buy_power', {}).get(currency, 0) > 1000:
        logger.info(f"  💳 使用购买力进行交易")
    else:
        logger.warning(f"  ⏭️ 账户资金异常，跳过交易")
        raise InsufficientFundsError(f"账户资金异常（显示为负数: ${available_cash:.2f}）")
```

### 修复后代码
```python
# 3. 资金检查
currency = "HKD" if ".HK" in symbol else "USD"
available_cash = account["cash"].get(currency, 0)
buy_power = account.get("buy_power", {}).get(currency, 0)
remaining_finance = account.get("remaining_finance", {}).get(currency, 0)

# 显示购买力和融资额度信息
logger.debug(
    f"  💰 {currency} 资金状态 - 可用: ${available_cash:,.2f}, "
    f"购买力: ${buy_power:,.2f}, 剩余融资额度: ${remaining_finance:,.2f}"
)

# 🔥 NEW: 跨币种债务诊断
if available_cash > 0 and buy_power < 0:
    other_ccy = "USD" if currency == "HKD" else "HKD"
    other_cash = account.get("cash", {}).get(other_ccy, 0)
    other_bp = account.get("buy_power", {}).get(other_ccy, 0)
    logger.warning(
        f"  🔥 {symbol}: 跨币种债务影响检测\n"
        f"     • {currency}: 现金=${available_cash:,.0f} ✅, 购买力=${buy_power:,.0f} ❌\n"
        f"     • {other_ccy}: 现金=${other_cash:,.0f}, 购买力=${other_bp:,.0f}\n"
        f"     • 原因: {other_ccy}账户可能出现负债或融资占用\n"
        f"     • 对策: 将使用现金估算可买数量（fallback机制）"
    )

if available_cash < 0:
    logger.error(
        f"  ❌ {symbol}: 资金异常（显示为负数: ${available_cash:.2f}）\n"
        f"     可能原因：融资账户或数据错误"
    )
    if account.get('buy_power', {}).get(currency, 0) > 1000:
        logger.info(f"  💳 使用购买力进行交易")
    else:
        logger.warning(f"  ⏭️ 账户资金异常，跳过交易")
        raise InsufficientFundsError(f"账户资金异常（显示为负数: ${available_cash:.2f}）")
```

### 改动说明
1. **跨币种检测**：检查本币种现金>0但买入力<0的情况
2. **输出诊断信息**：显示所有币种的现金和买入力状况
3. **解释原因**：帮助用户理解跨币种债务的影响
4. **非阻止性**：仅输出警告，不拒绝交易

### 日志示例
```
🔥 1398.HK: 跨币种债务影响检测
   • HKD: 现金=$690,292 ✅, 购买力=$-50,000 ❌
   • USD: 现金=$-50,000, 购买力=$0
   • 原因: USD账户可能出现负债或融资占用
   • 对策: 将使用现金估算可买数量（fallback机制）
```

---

## 补丁3：Rebalancer中的买入力监控

### 文件位置
`/data/web/longport-quant-new/src/longport_quant/risk/rebalancer.py`
第103-131行

### 原始代码
```python
for ccy, items in by_currency.items():
    equity = float(account.get("net_assets", {}).get(ccy, 0) or 0)
    if equity <= 0:
        continue

    # 计算当前持仓总市值
    total_value = 0.0
    values: Dict[str, float] = {}
    for p in items:
        sym = p["symbol"]
        price = price_map.get(sym, 0.0)
        qty = int(p.get("available_quantity") or p.get("quantity") or 0)
        if price > 0 and qty > 0:
            v = price * qty
            values[sym] = v
            total_value += v

    if total_value <= 0:
        continue

    # 目标持仓市值（预留现金 reserve）
    target_value = equity * (1.0 - reserve)
    if total_value <= target_value:
        logger.info(f"{ccy}: 当前持仓${total_value:,.0f} ≤ 目标${target_value:,.0f}，无需减仓")
        continue
```

### 修复后代码
```python
for ccy, items in by_currency.items():
    equity = float(account.get("net_assets", {}).get(ccy, 0) or 0)
    if equity <= 0:
        continue

    # 🔥 NEW: 检查买入力，若为负则强制增加预留
    buy_power = float(account.get("buy_power", {}).get(ccy, 0) or 0)
    current_reserve = reserve  # 记录原始预留比例
    if buy_power < 0:
        logger.warning(
            f"{ccy}: 购买力为负(${buy_power:,.0f}), "
            f"强制增加预留比例"
        )
        # 增加预留比例20%，上限80%
        reserve = min(reserve + 0.20, 0.80)
        if reserve > current_reserve:
            logger.info(
                f"     → 预留比例调整: {current_reserve*100:.0f}% → {reserve*100:.0f}% "
                f"(释放购买力)"
            )

    # 计算当前持仓总市值
    total_value = 0.0
    values: Dict[str, float] = {}
    for p in items:
        sym = p["symbol"]
        price = price_map.get(sym, 0.0)
        qty = int(p.get("available_quantity") or p.get("quantity") or 0)
        if price > 0 and qty > 0:
            v = price * qty
            values[sym] = v
            total_value += v

    if total_value <= 0:
        continue

    # 目标持仓市值（预留现金 reserve）
    target_value = equity * (1.0 - reserve)
    if total_value <= target_value:
        logger.info(f"{ccy}: 当前持仓${total_value:,.0f} ≤ 目标${target_value:,.0f}，无需减仓")
        continue
```

### 改动说明
1. **买入力检测**：在计算目标持仓前检查是否为负
2. **动态调整**：当买入力<0时，增加预留比例20%
3. **防止复发**：通过增加现金预留来释放购买力
4. **详细日志**：记录预留比例的调整过程

### 日志示例
```
⚠️ HKD: 购买力为负($-50,000), 强制增加预留比例
   → 预留比例调整: 30% → 50% (释放购买力)
   计算减仓目标: 减仓$500,000以释放购买力
```

---

## 实施顺序

### 第一步：应用补丁1（关键）
修改`_estimate_available_quantity()`方法，添加Fallback逻辑
- 文件：`scripts/order_executor.py`
- 行号：2082-2126
- 优先级：⚠️⚠️⚠️ 最高

### 第二步：应用补丁2（诊断）
添加跨币种债务检测
- 文件：`scripts/order_executor.py`
- 行号：1022-1032
- 优先级：⚠️⚠️ 中等

### 第三步：应用补丁3（预防）
增强Rebalancer的买入力监控
- 文件：`src/longport_quant/risk/rebalancer.py`
- 行号：103-131
- 优先级：⚠️ 低（长期优化）

---

## 验证步骤

### 1. 代码检查
```bash
# 验证补丁1
grep -n "Fallback当API返回0" scripts/order_executor.py

# 验证补丁2
grep -n "跨币种债务影响检测" scripts/order_executor.py

# 验证补丁3
grep -n "检查买入力" src/longport_quant/risk/rebalancer.py
```

### 2. 单元测试
```python
# 测试用例1：正常情况
account = {
    "cash": {"HKD": 100000},
    "buy_power": {"HKD": 100000},
    "remaining_finance": {"HKD": 500000}
}
# 预期：能正常估算

# 测试用例2：跨币种债务
account = {
    "cash": {"HKD": 100000, "USD": -50000},
    "buy_power": {"HKD": -50000, "USD": 0},
    "remaining_finance": {"HKD": 500000, "USD": 0}
}
# 预期：能触发fallback并用50%HKD现金估算

# 测试用例3：融资账户
account = {
    "cash": {"HKD": 50000},
    "buy_power": {"HKD": 500000},
    "remaining_finance": {"HKD": 500000}
}
# 预期：优先使用buy_power估算
```

### 3. 集成测试
```bash
# 重启订单执行器
systemctl restart order_executor

# 观察日志（等待出现HKD现金+买入力<0的场景）
tail -f logs/order_executor.log | grep -E "buy_power|Fallback|跨币种"

# 预期看到：
# ⚠️ buy_power=-50000(负值或不足), 改用现金fallback: 可买XXXX股
```

---

## 回滚方案

如果修复后出现问题，可按以下步骤回滚：

```bash
# 1. 使用git回滚
git checkout HEAD -- scripts/order_executor.py src/longport_quant/risk/rebalancer.py

# 2. 重启服务
systemctl restart order_executor

# 3. 验证恢复
tail -f logs/order_executor.log | grep "启动"
```

---

## 常见问题

### Q: Fallback会不会造成过度交易?
**A**: 不会。Fallback只用50%现金，且只在API返回0时触发。同时order_executor的其他风控仍会生效。

### Q: 为什么要用50%现金而不是100%?
**A**: 预留50%是为了:
- 保留一定的流动性应急
- 避免因其他币种负债而被强平
- 符合保守的风险管理原则

### Q: Rebalancer的预留比例增加会不会影响收益?
**A**: 会有一定影响，但这是为了安全性。当buy_power恢复正常后，预留比例自动恢复。

### Q: 跨币种债务是永久的吗?
**A**: 不是。通常是因为某个币种的头寸亏损或融资占用。解决方法：
- 平仓亏损头寸
- 补充现金
- 要求经纪商增加融资额度

