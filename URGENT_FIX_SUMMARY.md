# 紧急修复总结 - 文件描述符泄漏 + API 兼容性错误

**日期**: 2025-10-15
**状态**: 🔴 需要立即重启系统

---

## 🚨 当前问题

您的交易系统正在经历**严重的资源泄漏**：

```
[Errno 24] Too many open files
实时止损止盈检查失败 1347.HK: [Errno 24] Too many open files
```

**诊断结果**:
- 当前进程 (PID: 2507965) 打开了 **1025 个文件描述符**
- 其中 **1000+ 个 PostgreSQL 连接处于 CLOSE_WAIT 状态**（僵尸连接）
- 系统即将达到文件描述符限制并崩溃

**原因**: 当前运行的进程使用的是**旧版本代码**，存在数据库连接泄漏。

---

## ✅ 已完成的修复

### 1. 数据库连接池泄漏修复

**文件**: `src/longport_quant/persistence/stop_manager.py`

**优化内容**:
```python
# 连接池配置（已优化）
min_size=1,                            # 最小连接数
max_size=3,                            # 最大连接数（降低）
command_timeout=10,                    # 命令超时10秒
max_queries=1000,                      # 连接执行1000次查询后重建
max_inactive_connection_lifetime=60.0, # 非活动连接60秒后关闭
timeout=5.0,                           # 获取连接超时5秒
```

**优势**:
- ✅ 短生命周期，快速回收连接
- ✅ 自动重建连接，清理潜在问题
- ✅ 严格限制连接数量

### 2. 定期连接池重置

**文件**: `scripts/advanced_technical_trading.py:345`

**新增功能**:
```python
# 每10分钟自动重置连接池
if iteration % 10 == 0:
    await self.stop_manager.reset_pool()

    # 监控文件描述符数量
    fd_count = len(os.listdir(f'/proc/{pid}/fd'))
    if fd_count > 800:
        logger.error(f"⚠️ 文件描述符过多 ({fd_count})！")
```

**监控指标**:
- 🟢 正常: < 500
- 🟡 警告: 500-800
- 🔴 危险: > 800

### 3. API 兼容性修复

#### 错误 1: OrderStatus 枚举
```python
# ❌ 错误: Cancelled
# ✅ 修复: Canceled (美式拼写)
openapi.OrderStatus.Canceled
```

#### 错误 2: OrderType 枚举
```python
# ❌ 错误: Market, Limit
# ✅ 修复: MO, LO (缩写)
OrderType.MO  # Market Order
OrderType.LO  # Limit Order
```

#### 错误 3: 参数顺序错误
```python
# ✅ 修复: get_history_candles_by_offset 参数顺序
get_history_candles_by_offset(
    symbol,
    period,
    adjust_type,  # 第3位（已修正）
    offset,
    count
)
```

---

## 🚀 立即执行：重启系统

### 方法 1: 使用安全重启脚本（推荐）

```bash
bash scripts/safe_restart_trading.sh
```

**功能**:
- ✅ 自动检测并停止旧进程
- ✅ 显示文件描述符数量
- ✅ 启动新版本代码
- ✅ 提供多种日志输出选项
- ✅ 验证新进程运行状态

### 方法 2: 手动重启

```bash
# 1. 停止旧进程
pkill -f "advanced_technical_trading.py"

# 2. 确认进程已停止
ps aux | grep "advanced_technical_trading.py"

# 3. 启动新进程（输出到日志文件）
python3 scripts/advanced_technical_trading.py --builtin 2>&1 | tee trading_new.log
```

---

## 📊 验证修复效果

### 1. 启动后立即检查

```bash
# 查看日志，确认无错误
tail -f trading_new.log

# 应该看到:
# ✅ 数据库连接池已创建 (min=1, max=3, 短生命周期)
# ✅ 港股实时交易系统已启动
```

### 2. 启动资源监控（新终端）

```bash
python3 scripts/monitor_resources.py
```

**预期结果**:
```
时间                 文件描述符      网络连接     内存(MB)    CPU%
------------------------------------------------------------------
10:30:00            85              15          250.5      2.3
10:35:00            92              18          255.8      1.8
10:40:00            78              12          248.2      2.1
```

### 3. 每10分钟观察

日志中应该看到：
```
🔄 重置数据库连接池...
✅ 连接池已重置
📊 当前文件描述符数量: 92
```

---

## ⚠️ 警告信号

如果看到以下情况，请立即报告：

### 🔴 严重问题
- 文件描述符 > 800
- CLOSE_WAIT 连接 > 100
- 错误: `[Errno 24] Too many open files`

### 🟡 需要关注
- 文件描述符 > 500 且持续增长
- CLOSE_WAIT 连接 > 50
- 连接池重置失败

### 🟢 正常运行
- 文件描述符在 50-150 之间波动
- CLOSE_WAIT 连接 < 10
- 每10分钟成功重置连接池

---

## 🔧 如果问题持续

### 1. 检查数据库连接

```bash
# 查看 PostgreSQL 活跃连接
psql -U postgres -d longport_next_new -c "
SELECT count(*), state
FROM pg_stat_activity
GROUP BY state;
"
```

### 2. 检查系统限制

```bash
# 查看当前限制
ulimit -n

# 临时增加限制（如果需要）
ulimit -n 65536
```

### 3. 运行诊断脚本

```bash
# 详细诊断
bash scripts/diagnose_fd_leak.sh

# 查看连接状态
netstat -antp | grep postgres | grep CLOSE_WAIT | wc -l
```

---

## 📁 修改的文件清单

1. ✅ `src/longport_quant/persistence/stop_manager.py` - 连接池优化
2. ✅ `scripts/advanced_technical_trading.py` - 定期重置 + 监控
3. ✅ `src/longport_quant/execution/client.py` - OrderType 枚举修复
4. ✅ `src/longport_quant/execution/smart_router.py` - OrderType 枚举修复
5. ✅ `scripts/smart_position_rotation.py` - 参数顺序修复
6. ✅ `scripts/safe_restart_trading.sh` - 新增安全重启脚本

---

## 🎯 预期效果

修复后系统应该：

- ✅ 文件描述符稳定在 50-150 之间
- ✅ 无持续增长趋势
- ✅ 每10分钟自动重置连接池
- ✅ 无 OrderStatus/OrderType 枚举错误
- ✅ 无参数顺序错误
- ✅ PostgreSQL 连接正常关闭，无 CLOSE_WAIT 累积

---

## 📞 技术支持

如果重启后仍有问题：

1. **保存完整日志**:
   ```bash
   cp trading_new.log trading_error_$(date +%Y%m%d_%H%M%S).log
   ```

2. **收集诊断信息**:
   ```bash
   bash scripts/diagnose_fd_leak.sh > diagnosis_$(date +%Y%m%d_%H%M%S).txt
   ```

3. **提供以下信息**:
   - 完整错误日志
   - 诊断脚本输出
   - 进程 PID 和文件描述符数量
   - PostgreSQL 连接状态

---

## ⏰ 时间线

- **10:21** - 检测到文件描述符泄漏
- **10:25** - 分析确认 1000+ CLOSE_WAIT 连接
- **10:30** - 完成所有代码修复
- **10:35** - 🔴 **等待重启验证**

---

**🚨 请立即重启系统以应用修复！**

```bash
bash scripts/safe_restart_trading.sh
```
