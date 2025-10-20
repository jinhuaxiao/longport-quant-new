# 量化交易系统部署指南

## 系统要求

### 硬件要求
- CPU: 4核心以上
- 内存: 8GB以上
- 磁盘: 50GB以上SSD（用于历史数据存储）
- 网络: 稳定的互联网连接

### 软件要求
- 操作系统: Ubuntu 20.04+ / CentOS 8+ / macOS 12+
- Python: 3.11+
- PostgreSQL: 14+
- Redis: 6.0+（可选，用于缓存）

## 部署前检查清单

### 1. 环境准备 ✅
- [ ] Python 3.11+ 已安装
- [ ] PostgreSQL 14+ 已安装并运行
- [ ] 系统时区设置正确（建议使用 Asia/Shanghai）
- [ ] 网络可访问 LongPort OpenAPI

### 2. 账户配置 ✅
- [ ] LongPort 开发者账户已注册
- [ ] App Key 和 App Secret 已获取
- [ ] API 权限已开通（行情权限、交易权限）
- [ ] 测试环境和生产环境凭证分离

### 3. 代码部署 ✅
```bash
# 克隆代码
git clone https://github.com/your-repo/longport-quant.git
cd longport-quant

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或 venv\Scripts\activate  # Windows

# 安装依赖
pip install -e ".[dev]"
```

### 4. 配置文件 ✅
```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 文件，填入以下必要配置
# LONGPORT_APP_KEY=your_app_key
# LONGPORT_APP_SECRET=your_app_secret
# LONGPORT_REGION=cn  # 或 hk
# DATABASE_DSN=postgresql+asyncpg://user:pass@localhost/quant_db

# 配置监控列表
cp configs/watchlist.example.yml configs/watchlist.yml
# 编辑 configs/watchlist.yml 添加需要监控的股票
```

### 5. 数据库初始化 ✅
```bash
# 创建数据库
createdb quant_db

# 运行数据库迁移
alembic upgrade head

# 创建分区表（重要！）
python scripts/create_missing_partitions.py
python scripts/create_daily_partitions.py
```

## 初始化流程

### 方式一：一键初始化（推荐）
```bash
# 运行系统初始化脚本，自动完成所有初始化步骤
python scripts/init_system.py
```

### 方式二：分步初始化
```bash
# 1. 同步证券列表（港股和美股）
python scripts/sync_security_data.py hk --limit 50
python scripts/sync_security_data.py us --limit 30

# 2. 同步历史K线数据（最近2年）
python scripts/sync_historical_klines.py --years 2 --batch-size 5 --limit 20

# 3. 初始化监控列表
python scripts/init_watchlist.py

# 4. 验证数据完整性
python scripts/check_data.py
```

## 系统验证

### 验证步骤
```bash
# 1. 运行生产环境验证脚本
python scripts/validate_production.py

# 2. 检查验证结果
# ✅ 所有测试通过 - 可以启动生产环境
# ⚠️ 有警告 - 可以启动但需要关注
# ❌ 有关键错误 - 必须先修复问题
```

### 验证项目说明
1. **数据库连接**: 验证数据库是否可访问
2. **数据库表结构**: 检查所有必需表和分区是否存在
3. **数据可用性**: 验证是否有足够的历史数据
4. **API连接**: 测试 LongPort API 是否可用
5. **监控列表**: 确认监控股票配置正确
6. **实时行情**: 测试实时数据获取（交易时间）
7. **调度器准备**: 检查投资组合和策略配置
8. **性能测试**: 测试数据库和API响应时间

## 启动服务

### 1. 启动调度器（核心服务）
```bash
# 生产模式
python scripts/run_scheduler.py --mode run

# 测试模式（只打印不执行）
python scripts/run_scheduler.py --mode test

# 后台运行（使用 systemd 或 supervisor）
# 见下方 systemd 配置示例
```

### 2. 启动实时数据同步（可选）
```bash
# 实时K线数据同步
python scripts/sync_realtime_data.py

# 或使用调度器的内置任务（推荐）
# 调度器会自动按计划同步数据
```

## 系统服务配置

### systemd 服务配置
创建文件 `/etc/systemd/system/quant-scheduler.service`:

```ini
[Unit]
Description=Quantitative Trading Scheduler
After=network.target postgresql.service

[Service]
Type=simple
User=quant
Group=quant
WorkingDirectory=/opt/longport-quant
Environment="PATH=/opt/longport-quant/venv/bin"
ExecStart=/opt/longport-quant/venv/bin/python scripts/run_scheduler.py --mode run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

启用并启动服务:
```bash
sudo systemctl daemon-reload
sudo systemctl enable quant-scheduler
sudo systemctl start quant-scheduler
sudo systemctl status quant-scheduler
```

## 监控和日志

### 日志位置
- 应用日志: `logs/quant.log`
- 调度器日志: `logs/scheduler.log`
- 错误日志: `logs/error.log`

### 监控指标
```bash
# 检查系统状态
python scripts/check_system_status.py

# 查看最新K线数据
python scripts/check_klines.py

# 查看持仓和订单
python scripts/check_positions.py
```

### 告警配置
建议配置以下告警:
1. 数据同步延迟 > 10分钟
2. API调用失败率 > 5%
3. 数据库连接失败
4. 磁盘空间 < 10GB
5. 策略执行异常

## 维护任务

### 日常维护
```bash
# 每日任务（建议定时执行）
python scripts/create_missing_partitions.py  # 创建新分区
python scripts/clean_old_data.py --days 365  # 清理旧数据

# 每周任务
python scripts/optimize_database.py  # 数据库优化
python scripts/backup_database.py    # 数据备份
```

### 数据备份
```bash
# 备份数据库
pg_dump quant_db > backup_$(date +%Y%m%d).sql

# 恢复数据库
psql quant_db < backup_20240101.sql
```

## 故障排查

### 常见问题

#### 1. 数据库连接失败
```bash
# 检查 PostgreSQL 服务
sudo systemctl status postgresql

# 检查连接字符串
python -c "from longport_quant.config import get_settings; print(get_settings().database_dsn)"

# 测试连接
psql -d quant_db -c "SELECT 1"
```

#### 2. API认证失败
```bash
# 检查环境变量
python -c "import os; print('LONGPORT_APP_KEY' in os.environ)"

# 验证API凭证
python scripts/test_api_connection.py
```

#### 3. 数据同步失败
```bash
# 检查网络连接
ping api.longportapp.com

# 检查API限流
# 减少批次大小或增加延迟

# 手动同步单个股票
python scripts/sync_historical_klines.py --symbols 700.HK --years 1
```

#### 4. 分区表错误
```bash
# 创建缺失的分区
python scripts/create_missing_partitions.py

# 检查分区状态
psql -d quant_db -c "SELECT tablename FROM pg_tables WHERE tablename LIKE 'kline_%'"
```

## 性能优化

### 数据库优化
```sql
-- 创建索引
CREATE INDEX IF NOT EXISTS idx_kline_daily_symbol_timestamp
ON kline_daily(symbol, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_kline_minute_symbol_timestamp
ON kline_minute(symbol, timestamp DESC);

-- 更新统计信息
ANALYZE;

-- 清理碎片
VACUUM ANALYZE;
```

### 应用优化
```python
# 配置建议（.env文件）
DATABASE_POOL_SIZE=20        # 数据库连接池大小
DATABASE_MAX_OVERFLOW=10     # 最大溢出连接数
API_RATE_LIMIT=10            # API调用频率限制（次/秒）
BATCH_SIZE=10                # 批处理大小
```

## 安全建议

1. **凭证管理**
   - 使用环境变量或密钥管理服务存储敏感信息
   - 定期轮换 API 密钥
   - 分离测试和生产环境凭证

2. **网络安全**
   - 使用防火墙限制数据库访问
   - 启用 PostgreSQL SSL 连接
   - 配置 API 调用白名单

3. **数据安全**
   - 定期备份数据库
   - 加密敏感数据
   - 实施访问控制

4. **监控告警**
   - 监控异常登录
   - 检测异常交易模式
   - 设置资金风控限制

## 升级流程

```bash
# 1. 备份当前版本
cp -r /opt/longport-quant /opt/longport-quant.backup

# 2. 获取新版本
git pull origin main

# 3. 更新依赖
pip install -e ".[dev]" --upgrade

# 4. 运行数据库迁移
alembic upgrade head

# 5. 验证系统
python scripts/validate_production.py

# 6. 重启服务
sudo systemctl restart quant-scheduler
```

## 联系支持

- 技术文档: [docs/](docs/)
- 问题反馈: GitHub Issues
- 紧急支持: 查看 .env 中的 SUPPORT_EMAIL

## 附录：检查清单总结

### 部署前检查 ✅
- [ ] 环境依赖安装完成
- [ ] 配置文件准备就绪
- [ ] 数据库初始化成功
- [ ] API凭证验证通过

### 数据初始化 ✅
- [ ] 证券列表同步完成
- [ ] 历史K线数据就绪
- [ ] 监控列表配置完成
- [ ] 数据完整性验证通过

### 系统验证 ✅
- [ ] 生产环境验证脚本通过
- [ ] 实时数据获取正常
- [ ] 调度器测试模式运行成功
- [ ] 性能指标符合要求

### 上线检查 ✅
- [ ] 服务配置完成
- [ ] 监控告警就绪
- [ ] 备份策略实施
- [ ] 应急预案准备

---
最后更新: 2024-01-01