# 文件描述符泄漏修复总结

## 问题诊断

### 症状
```
[Errno 24] Too many open files
```

### 根本原因
通过诊断脚本发现：
- **1009 个 PostgreSQL 连接处于 CLOSE_WAIT 状态**
- 所有连接指向 `localhost:postgresql`
- 僵尸连接未被清理，持续累积

`CLOSE_WAIT` 状态说明：
- PostgreSQL 服务器已关闭连接
- Python 客户端未正确关闭 socket
- 连接池管理存在问题

## 修复措施

### 1. 优化连接池配置

**文件**: `src/longport_quant/persistence/stop_manager.py`

```python
# 之前的配置
min_size=2, max_size=10, command_timeout=60

# 优化后的配置
min_size=1,                            # 降低最小连接数
max_size=3,                            # 降低最大连接数
command_timeout=10,                    # 缩短超时时间
max_queries=1000,                      # 限制每个连接的查询数
max_inactive_connection_lifetime=60.0, # 1分钟后关闭非活动连接
max_cached_statement_lifetime=0,       # 不缓存prepared statements
timeout=5.0,                           # 获取连接超时5秒
```

**优势**:
- 短生命周期，快速回收连接
- 限制连接总数，避免资源耗尽
- 定期重建连接，清理潜在问题

### 2. 添加连接池重置机制

**新增方法**:
```python
async def reset_pool(self):
    """重置连接池（清理僵尸连接）"""
    logger.info("🔄 重置数据库连接池...")
    await self.disconnect()
    await self.connect()
    logger.success("✅ 连接池已重置")
```

**主循环定期调用**:
```python
# 每30分钟重置一次连接池
if iteration % 30 == 0:
    await self.stop_manager.reset_pool()
```

### 3. 添加资源清理逻辑

**文件**: `scripts/advanced_technical_trading.py`

```python
finally:
    # 清理资源
    if hasattr(trader, 'stop_manager') and trader.stop_manager:
        await trader.stop_manager.disconnect()
```

确保程序退出时正确关闭所有连接。

### 4. 增强异常处理

所有数据库操作都使用 `async with self.pool.acquire()`:
```python
async with self.pool.acquire() as conn:
    row = await conn.fetchrow(...)
```

即使出现异常，连接也会被正确释放回连接池。

## 诊断工具

### 1. 资源监控脚本
```bash
python3 scripts/monitor_resources.py
```

实时监控：
- 文件描述符数量
- 网络连接数量
- 内存使用
- CPU 使用率

### 2. 泄漏诊断脚本
```bash
bash scripts/diagnose_fd_leak.sh
```

诊断内容：
- 文件描述符分类统计
- TCP 连接状态分析
- CLOSE_WAIT 连接检测
- 连接目标分析

### 3. 便捷重启脚本
```bash
./scripts/restart_with_monitor.sh
```

功能：
- 停止旧进程
- 启动新进程
- 选择监控方式（日志/资源/分屏）

## 验证步骤

### 1. 重启交易系统

```bash
# 停止旧进程
pkill -f "advanced_technical_trading.py"

# 启动新进程
python3 scripts/advanced_technical_trading.py --builtin 2>&1 | tee trading_new.log
```

### 2. 启动资源监控（另一个终端）

```bash
python3 scripts/monitor_resources.py
```

### 3. 观察指标

正常运行应该：
- ✅ 文件描述符稳定在 50-100 之间
- ✅ 没有持续增长趋势
- ✅ CLOSE_WAIT 连接数为 0

如果出现问题：
- ⚠️ 文件描述符 > 500
- ⚠️ 持续增长趋势
- ❌ CLOSE_WAIT 连接 > 10

### 4. 定期检查

```bash
# 每小时运行一次
bash scripts/diagnose_fd_leak.sh
```

## 预期效果

修复后应该看到：

```
时间                 文件描述符      网络连接     内存(MB)    CPU%    线程
------------------------------------------------------------------
17:00:00            85              15          250.5      2.3     12
17:05:00            92              18          255.8      1.8     12
17:10:00            78              12          248.2      2.1     12
17:15:00            88              16          252.6      1.9     12
```

- 文件描述符在合理范围内波动（50-150）
- 没有持续增长趋势
- 定期重置后回落到低位

## 如果问题持续

如果修复后仍有问题：

1. **检查 PostgreSQL 配置**
   ```sql
   SHOW max_connections;
   SELECT count(*) FROM pg_stat_activity;
   ```

2. **增加系统限制**
   ```bash
   ulimit -n 65536
   ```

3. **启用详细日志**
   ```python
   # 在 stop_manager.py 中
   import logging
   logging.basicConfig(level=logging.DEBUG)
   ```

4. **联系我获取进一步支持**

## 相关文件

- `src/longport_quant/persistence/stop_manager.py` - 连接池管理
- `scripts/advanced_technical_trading.py` - 主交易脚本
- `scripts/monitor_resources.py` - 资源监控
- `scripts/diagnose_fd_leak.sh` - 泄漏诊断
- `scripts/restart_with_monitor.sh` - 便捷重启

## 技术细节

### asyncpg 连接池参数说明

- `min_size`: 池中保持的最小连接数
- `max_size`: 池中允许的最大连接数
- `command_timeout`: SQL 命令执行超时时间
- `max_queries`: 连接执行多少次查询后重建
- `max_inactive_connection_lifetime`: 非活动连接存活时间
- `timeout`: 从池中获取连接的超时时间

### CLOSE_WAIT 状态

TCP 连接关闭的四次握手：
1. 远程端发送 FIN（PostgreSQL 关闭连接）
2. 本地端发送 ACK（确认收到）→ 进入 CLOSE_WAIT 状态
3. 本地端应该发送 FIN（关闭连接）
4. 远程端发送 ACK

如果本地端没有执行步骤3，连接会永远停留在 CLOSE_WAIT 状态。

## 更新日志

- 2025-10-14: 初始修复
  - 优化连接池配置
  - 添加重置机制
  - 添加监控工具
  - 添加清理逻辑
