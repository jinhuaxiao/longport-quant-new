# 错误修复总结

## 问题描述
运行交易脚本时遇到两个错误：
1. `OrderManager.save_order()` 参数错误
2. `SlackNotifier` 没有 `send_message` 方法

## 错误详情

### 错误1: OrderManager参数错误
```
TypeError: OrderManager.save_order() missing 5 required positional arguments: 'symbol', 'side', 'quantity', 'price', and 'status'
```

### 错误2: SlackNotifier方法错误
```
AttributeError: 'SlackNotifier' object has no attribute 'send_message'
```

## 修复方案

### 1. 修复 OrderManager.save_order 调用

**错误原因**：传递了字典而不是单独的参数

**修复前**：
```python
await self.order_manager.save_order({
    "order_id": order_id,
    "symbol": symbol,
    "side": "BUY",
    "quantity": quantity,
    "price": signal['price'],
    "strategy": signal['strategy'],
    # ...
})
```

**修复后**：
```python
await self.order_manager.save_order(
    order_id=order_id,
    symbol=symbol,
    side="BUY",
    quantity=quantity,
    price=signal['price'],
    status="New"  # 必需参数
)
```

### 2. 修复 SlackNotifier 方法调用

**错误原因**：使用了错误的方法名

**修复前**：
```python
await self.slack.send_message(message)
```

**修复后**：
```python
await self.slack.send(message)
```

### 3. 修复 Slack 初始化

**错误原因**：没有正确获取webhook URL

**修复前**：
```python
self.slack = SlackNotifier(self.settings)  # settings不是webhook URL
```

**修复后**：
```python
webhook_url = self.settings.slack_webhook_url if hasattr(self.settings, 'slack_webhook_url') else None
if webhook_url:
    self.slack = SlackNotifier(webhook_url)
    logger.info("✅ Slack通知已启用")
else:
    logger.warning("⚠️ Slack webhook URL未配置")
    self.slack = None
```

## 配置说明

### Slack配置（可选）
在 `configs/settings.toml` 中添加：
```toml
[slack]
webhook_url = "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
```

## 运行测试

### 1. 模拟模式（推荐先测试）
```bash
# 不发送Slack通知的模拟模式
python scripts/momentum_breakthrough_trading.py --builtin --dry-run --no-slack

# 启用Slack的模拟模式
python scripts/momentum_breakthrough_trading.py --builtin --dry-run
```

### 2. 实盘模式
```bash
# 实盘交易（确保配置正确）
python scripts/momentum_breakthrough_trading.py --builtin
```

## 测试结果
✅ 模拟模式运行正常
✅ 信号识别正常（NVDA 60分强突破信号）
✅ OrderManager参数正确传递
✅ SlackNotifier方法调用正确
✅ 错误处理正常

## 修改的文件
- `/data/web/longport-quant-new/scripts/momentum_breakthrough_trading.py`

## 关键学习点

1. **API参数传递**
   - `OrderManager.save_order()` 需要位置参数，不接受字典
   - 必需参数：order_id, symbol, side, quantity, price, status

2. **SlackNotifier API**
   - 正确方法名是 `send()`，不是 `send_message()`
   - 初始化需要webhook URL字符串，不是settings对象

3. **错误处理**
   - 始终在try-except中包装API调用
   - 提供清晰的错误日志
   - 在Slack不可用时优雅降级