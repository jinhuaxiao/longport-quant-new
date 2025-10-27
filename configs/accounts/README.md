# 多账号配置说明

## 目录结构

```
configs/accounts/
├── paper_001.env    # 模拟账号配置（已配置）
├── live_001.env     # 真实账号配置（需要用户填写）
└── README.md        # 本文件
```

## 如何配置真实账号

1. 打开 `live_001.env` 文件
2. 替换以下占位符为您的真实账号API凭证：
   - `YOUR_LIVE_APP_KEY_HERE` → 您的真实账号APP KEY
   - `YOUR_LIVE_APP_SECRET_HERE` → 您的真实账号APP SECRET
   - `YOUR_LIVE_ACCESS_TOKEN_HERE` → 您的真实账号ACCESS TOKEN

3. 保存文件

## 如何获取Longport真实账号API凭证

1. 登录 [Longport开放平台](https://open.longportapp.com)
2. 进入"应用管理"页面
3. 创建或选择一个应用
4. 获取 APP KEY 和 APP SECRET
5. 生成 ACCESS TOKEN

⚠️ **安全提示**：
- 请妥善保管您的API凭证，不要泄露或提交到公共代码仓库
- 建议为真实账号设置IP白名单
- 定期更换ACCESS TOKEN

## 账号命名规范

- 模拟账号：`paper_XXX`（如 paper_001, paper_002）
- 真实账号：`live_XXX`（如 live_001, live_002）

## 启动指定账号

```bash
# 启动模拟账号
python3 scripts/signal_generator.py --account-id paper_001
python3 scripts/order_executor.py --account-id paper_001

# 启动真实账号
python3 scripts/signal_generator.py --account-id live_001
python3 scripts/order_executor.py --account-id live_001
```

## 账号隔离机制

每个账号拥有独立的：
- ✅ API凭证和交易权限
- ✅ Redis信号队列（如 `trading:signals:paper_001`）
- ✅ 日志文件（如 `logs/order_executor_paper_001.log`）
- ✅ 运行进程
