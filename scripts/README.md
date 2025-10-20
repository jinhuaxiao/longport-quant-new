# 量化交易系统脚本说明

## 核心脚本

### 初始化和部署

- **`init_system.py`** - 一键系统初始化
  ```bash
  python scripts/init_system.py
  ```
  自动完成环境检查、数据库初始化、数据同步等所有初始化步骤

- **`validate_production.py`** - 生产环境验证
  ```bash
  python scripts/validate_production.py
  ```
  验证系统是否已准备好投入生产使用

- **`system_status.py`** - 系统状态总览
  ```bash
  python scripts/system_status.py
  ```
  显示系统当前状态和统计信息

### 数据同步

- **`sync_security_data.py`** - 同步证券列表
  ```bash
  python scripts/sync_security_data.py hk --limit 50  # 同步港股
  python scripts/sync_security_data.py us --limit 30  # 同步美股
  ```

- **`sync_historical_klines.py`** - 同步历史K线
  ```bash
  python scripts/sync_historical_klines.py --years 2 --batch-size 5 --limit 20
  ```

- **`sync_realtime_data.py`** - 实时数据同步
  ```bash
  python scripts/sync_realtime_data.py
  ```

### 数据库管理

- **`create_database.py`** - 创建数据库表
- **`create_missing_partitions.py`** - 创建缺失的分区表
- **`create_daily_partitions.py`** - 创建日线分区表
- **`verify_tables.py`** - 验证表结构

### 监控和检查

- **`check_data.py`** - 检查数据完整性
- **`check_klines.py`** - 检查K线数据
- **`e2e_test.py`** - 端到端测试
  ```bash
  python scripts/e2e_test.py
  ```

### 调度器

- **`run_scheduler.py`** - 主调度器
  ```bash
  python scripts/run_scheduler.py --mode test  # 测试模式
  python scripts/run_scheduler.py --mode run   # 生产模式
  ```

### 策略回测

- **`run_backtest.py`** - 运行策略回测
  ```bash
  python scripts/run_backtest.py
  ```

### 配置管理

- **`init_watchlist.py`** - 初始化监控列表
  ```bash
  python scripts/init_watchlist.py
  ```

## 使用流程

### 新系统部署

1. **环境准备**
   ```bash
   cp .env.example .env
   # 编辑 .env 填入配置
   ```

2. **一键初始化**
   ```bash
   python scripts/init_system.py
   ```

3. **验证系统**
   ```bash
   python scripts/validate_production.py
   ```

4. **启动服务**
   ```bash
   python scripts/run_scheduler.py --mode run
   ```

### 日常运维

- **查看状态**
  ```bash
  python scripts/system_status.py
  ```

- **数据更新**
  ```bash
  python scripts/sync_historical_klines.py --symbols 700.HK --years 1
  ```

- **运行测试**
  ```bash
  python scripts/e2e_test.py
  ```

### 故障排查

1. **检查系统状态**
   ```bash
   python scripts/system_status.py
   ```

2. **验证环境**
   ```bash
   python scripts/validate_production.py
   ```

3. **查看数据**
   ```bash
   python scripts/check_data.py
   python scripts/check_klines.py
   ```

## 脚本依赖

所有脚本都依赖于：
- Python 3.11+
- PostgreSQL 14+
- 项目依赖包（通过 `pip install -e ".[dev]"` 安装）
- 正确配置的 `.env` 文件

## 注意事项

1. **首次运行**：建议使用 `init_system.py` 进行完整初始化
2. **生产环境**：运行 `validate_production.py` 确认系统就绪
3. **数据同步**：注意API限流，使用适当的批次大小
4. **分区管理**：定期运行 `create_missing_partitions.py` 创建新分区
5. **监控告警**：建议配置日志监控和异常告警