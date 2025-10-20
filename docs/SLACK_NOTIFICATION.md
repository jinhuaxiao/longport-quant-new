# Slack 通知配置指南

## 概述

高级技术指标交易系统 (`advanced_technical_trading.py`) 已集成Slack通知功能，可以实时推送以下类型的交易信息：

1. **交易信号** - 当系统检测到买入信号时
2. **订单执行** - 当开仓/平仓订单提交时
3. **止损触发** - 当价格触及止损位时
4. **止盈触发** - 当价格触及止盈位时

## 配置方法

### 方法1: 使用 .env 文件（推荐）

在项目根目录的 `.env` 文件中添加：

```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

### 方法2: 使用 configs/settings.toml

在 `configs/settings.toml` 中添加：

```toml
slack_webhook_url = "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
```

## 获取 Slack Webhook URL

### 步骤1: 创建 Slack App

1. 访问 https://api.slack.com/apps
2. 点击 "Create New App"
3. 选择 "From scratch"
4. 输入应用名称（如 "Trading Bot"）和选择工作区
5. 点击 "Create App"

### 步骤2: 启用 Incoming Webhooks

1. 在应用设置页面，点击左侧菜单的 "Incoming Webhooks"
2. 将 "Activate Incoming Webhooks" 开关打开
3. 点击底部的 "Add New Webhook to Workspace"
4. 选择要发送消息的频道
5. 点击 "Allow"

### 步骤3: 复制 Webhook URL

1. 在 "Webhook URLs for Your Workspace" 部分，复制显示的 URL
2. URL 格式类似：`https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXX`
3. 将此URL配置到 `.env` 或 `settings.toml` 中

## 通知消息格式

### 1. 交易信号通知

```
🚀 STRONG_BUY 信号: AAPL.US

💯 综合评分: 85/100
💵 当前价格: $254.43
📊 RSI: 28.5 | MACD: 1.234
📉 布林带位置: 15.2%
📈 成交量比率: 2.3x
🎯 止损: $240.00 (-5.7%)
🎁 止盈: $270.00 (+6.1%)
📌 趋势: bullish
💡 原因: RSI超卖, 价格接近下轨, MACD金叉, 成交量放大
```

### 2. 开仓订单通知

```
📈 开仓订单已提交

📋 订单ID: order_12345
📊 标的: AAPL.US
💯 类型: STRONG_BUY (评分: 85/100)
📦 数量: 20股
💵 价格: $254.43
💰 总额: $5088.60
🎯 止损位: $240.00 (-5.7%)
🎁 止盈位: $270.00 (+6.1%)
📌 ATR: $4.81
```

### 3. 止损触发通知

```
🛑 止损触发: AAPL.US

💵 入场价: $254.43
💸 当前价: $240.00
🎯 止损位: $240.00
📉 盈亏: -5.67%
⚠️ 将执行卖出操作
```

### 4. 止盈触发通知

```
🎉 止盈触发: AAPL.US

💵 入场价: $254.43
💰 当前价: $270.00
🎁 止盈位: $270.00
📈 盈亏: +6.12%
✅ 将执行卖出操作
```

### 5. 平仓订单通知

```
✅ 平仓订单已提交

📋 订单ID: order_12346
📊 标的: AAPL.US
📝 原因: 止盈
📦 数量: 20股
💵 入场价: $254.43
💰 平仓价: $270.00
💹 盈亏: $311.40 (+6.12%)
```

## 测试通知功能

运行测试脚本验证配置是否正确：

```bash
python3 scripts/test_slack_notification.py
```

该脚本会发送6条测试消息到你配置的Slack频道：
1. 简单测试消息
2. 交易信号格式
3. 开仓订单格式
4. 止损触发格式
5. 止盈触发格式
6. 平仓订单格式

## 运行带Slack通知的交易系统

配置完成后，正常运行交易系统即可：

```bash
python3 scripts/advanced_technical_trading.py
```

系统会自动在以下情况发送Slack通知：
- 检测到交易信号（评分≥30分）
- 提交开仓订单
- 触发止损/止盈
- 提交平仓订单

## 关闭通知

如果不想使用Slack通知，只需：
1. 不配置 `SLACK_WEBHOOK_URL`
2. 或将其设置为空值

系统会自动跳过通知发送，不影响正常交易功能。

## 注意事项

1. **Webhook URL安全**
   - 不要将Webhook URL提交到版本控制系统
   - `.env` 文件应该在 `.gitignore` 中

2. **消息频率**
   - 系统每60秒扫描一次
   - 只在检测到信号或触发止损止盈时发送消息
   - 不会产生消息轰炸

3. **错误处理**
   - 如果Slack通知发送失败，不会影响交易执行
   - 错误会记录在日志中

4. **多频道通知**
   - 如需向多个频道发送，创建多个Webhook
   - 或使用Slack App的更高级功能

## 故障排查

### 问题：收不到通知

**检查项：**
1. Webhook URL配置是否正确
2. 运行测试脚本检查配置：`python3 scripts/test_slack_notification.py`
3. 检查Slack频道权限
4. 查看系统日志是否有错误信息

### 问题：通知格式错乱

**解决：**
- Slack使用mrkdwn格式
- 确保消息中的特殊字符正确转义
- 星号`*`用于加粗，反引号用于代码格式

### 问题：通知延迟

**原因：**
- 网络延迟
- Slack API限流

**解决：**
- 通知是异步发送，不会阻塞交易
- 如有延迟不影响交易执行

## 进阶配置

### 自定义消息格式

修改 `scripts/advanced_technical_trading.py` 中的通知消息：

```python
# 在 _display_signal 方法中自定义交易信号格式
message = (
    f"{emoji} *{signal['type']}* 信号: {symbol}\n\n"
    # 添加你想要的信息...
)
```

### 添加通知过滤

只在特定条件下发送通知：

```python
# 只在强买信号时通知
if signal['type'] == 'STRONG_BUY' and self.slack:
    await self.slack.send(message)
```

### 集成其他通知渠道

参考 `src/longport_quant/notifications/slack.py` 创建类似的通知类：
- Email 通知
- 微信通知
- Telegram 通知
- Discord 通知

## 示例配置文件

### .env 示例

```bash
# Longport API
LONGPORT_APP_KEY=your_app_key
LONGPORT_APP_SECRET=your_app_secret
LONGPORT_ACCESS_TOKEN=your_access_token

# Slack 通知
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T017BFHLZED/B090K3HM46S/xxxx

# 数据库
DATABASE_DSN=postgresql+asyncpg://user:pass@localhost:5432/longport
```

### configs/settings.toml 示例

```toml
environment = "production"
timezone = "Asia/Hong_Kong"

slack_webhook_url = "https://hooks.slack.com/services/T017BFHLZED/B090K3HM46S/xxxx"

database_dsn = "postgresql+asyncpg://user:pass@localhost:5432/longport"

watchlist_path = "configs/watchlist.yml"
```

## 相关文件

- `src/longport_quant/notifications/slack.py` - Slack通知实现
- `src/longport_quant/config/settings.py` - 配置管理
- `scripts/advanced_technical_trading.py` - 交易系统主程序
- `scripts/test_slack_notification.py` - 通知测试脚本

## 支持

如有问题，请参考：
1. Slack API文档: https://api.slack.com/messaging/webhooks
2. 项目README: `README.md`
3. 交易策略文档: `docs/ADVANCED_STRATEGY_GUIDE.md`