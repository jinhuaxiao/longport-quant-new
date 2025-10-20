# 资金检查逻辑修复总结

## 问题描述
用户反馈在运行 `momentum_breakthrough_trading.py` 时，美股标的显示"资金不足"错误，但实际账户有购买力和融资额度。

## 问题原因分析

### 1. 原始代码问题
- 脚本只检查了现金余额 (`total_cash`)，没有使用购买力 (`buy_power`)
- 对于融资账户，现金可能是负数（如本例中 HKD 现金为 -448,805.40）
- 没有正确处理跨币种交易（长桥支持用港币购买美股）

### 2. 账户实际情况
通过运行 `check_account_fields.py` 发现：
```
HKD 账户状态：
- total_cash: -448,805.40 (负数，因为使用了融资)
- buy_power: 594,563.96 (实际可用购买力)
- max_finance_amount: 3,200,000.00 (最大融资额度)
- remaining_finance_amount: 2,435,456.18 (剩余可用融资)
```

## 解决方案

### 1. 修改账户状态检查 (`check_account_status`)
```python
# 修改前：只返回 cash
return {
    "cash": cash,
    "positions": positions,
    "position_count": len(positions)
}

# 修改后：返回 buy_power
return {
    "buy_power": buy_power,  # 购买力（包含融资）
    "cash": total_cash,       # 实际现金（可能为负）
    "positions": positions,
    "position_count": len(positions),
    "net_assets": net_assets
}
```

### 2. 修改信号执行逻辑 (`execute_signal`)
```python
# 修改前：使用 cash
available_cash = account['cash'].get(currency, 0)

# 修改后：使用 buy_power
available_power = account['buy_power'].get(currency, 0)

# 支持跨币种交易
if currency == "USD" and available_power < 1000:
    hkd_power = account['buy_power'].get("HKD", 0)
    if hkd_power > 0:
        available_power = hkd_power / 7.8  # 转换为等值美元
        use_hkd_for_usd = True
```

### 3. 关键改进点
1. **使用购买力而非现金**：`buy_power` 包含了融资额度，更准确反映可用资金
2. **支持跨币种交易**：长桥允许用港币购买美股，需要进行汇率转换
3. **正确处理融资账户**：融资账户的现金可能为负，但仍有购买力

## 测试结果
修复后的脚本成功执行：
- ✅ 正确识别了 HKD 购买力：$594,563.96
- ✅ 成功为美股计算可用资金：约 $76,226 USD
- ✅ 生成了 NVDA.US 的买入信号并执行
- ✅ 其他股票信号也正常生成

## 建议的后续优化

1. **更精确的汇率处理**
   - 从 API 获取实时汇率而非硬编码 7.8

2. **增加资金管理日志**
   - 在每次交易前打印详细的资金状态
   - 记录使用的是哪种货币和资金来源

3. **风险控制**
   - 设置融资使用上限比例
   - 监控保证金比率避免 margin call

4. **配置化**
   - 将跨币种交易开关做成配置项
   - 允许用户设置偏好货币优先级

## 相关文件
- `/data/web/longport-quant-new/scripts/momentum_breakthrough_trading.py` - 主交易脚本（已修复）
- `/data/web/longport-quant-new/scripts/check_account_fields.py` - 账户字段检查工具
- `/data/web/longport-quant-new/src/longport_quant/execution/client.py` - 交易客户端封装