# Slack 通知快速设置 (5分钟)

## 1. 获取 Slack Webhook URL

1. 访问 https://api.slack.com/apps → **Create New App**
2. 选择 **"From scratch"**
3. 输入应用名称（如 "Trading Bot"）→ 选择工作区 → **Create App**
4. 左侧菜单点击 **"Incoming Webhooks"** → 打开开关
5. 点击 **"Add New Webhook to Workspace"** → 选择频道 → **Allow**
6. 复制生成的 Webhook URL

## 2. 配置到项目

在项目根目录的 `.env` 文件中添加：

```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

## 3. 测试

```bash
python3 scripts/test_slack_notification.py
```

## 4. 运行交易系统

```bash
python3 scripts/advanced_technical_trading.py
```

## 通知内容

系统会在以下情况自动发送Slack消息：

- 🚀 **交易信号** - 检测到买入机会（含RSI、布林带、MACD分析）
- 📈 **开仓订单** - 提交买入订单（含止损止盈位）
- 🛑 **止损触发** - 价格跌破止损位
- 🎉 **止盈触发** - 价格达到止盈位
- ✅ **平仓订单** - 完成卖出操作（含盈亏统计）

## 不想使用？

不配置 `SLACK_WEBHOOK_URL` 即可，系统会自动跳过通知，不影响交易功能。

---

详细配置指南: [docs/SLACK_NOTIFICATION.md](docs/SLACK_NOTIFICATION.md)