# 关键Bug修复：信号删除失败导致僵尸信号

## 紧急程度：🔴 严重

## 问题描述

信号处理完成后无法从 `processing` 队列删除，导致：
1. 信号永久卡在 `processing` 队列
2. 超过5分钟后被自动恢复到主队列
3. 形成死循环：处理 → 删除失败 → 恢复 → 重复处理
4. 队列监控显示大量"处理中"信号

## 根本原因

**JSON 序列化不匹配导致 Redis ZREM 删除失败**

### 代码分析

#### 问题代码（修复前）

```python
# consume_signal() - 存入processing队列
signal_json, score = result[0]
signal = self._deserialize_signal(signal_json)

# 添加字段（修改了signal对象！）
signal['processing_started_at'] = datetime.now().isoformat()

# 存入processing队列（使用原始JSON）
await redis.zadd(processing_key, {signal_json: time.time()})

# mark_signal_completed() - 从processing队列删除
signal_json = self._serialize_signal(signal)  # ❌ 包含了新增的字段
await redis.zrem(processing_key, signal_json)  # ❌ 删除失败！
```

### 问题示意

```
原始JSON:
{"symbol": "0857.HK", "type": "BUY", "score": 58, ...}

修改后的JSON（包含processing_started_at）:
{"symbol": "0857.HK", "type": "BUY", "score": 58, ..., "processing_started_at": "2025-10-17T09:59:15"}

Redis ZREM:
- 查找: {"symbol": "0857.HK", ..., "processing_started_at": "..."}
- 队列中存的: {"symbol": "0857.HK", "type": "BUY", "score": 58, ...}
- 结果: 找不到匹配 ❌
```

## 修复方案

### 解决方案：保存原始JSON引用

在 `consume_signal()` 中保存原始 JSON，`mark_signal_completed()` 使用原始 JSON 删除。

#### 修复后的代码

```python
# consume_signal()
signal_json, score = result[0]
signal = self._deserialize_signal(signal_json)

# ✅ 保存原始JSON
signal['_original_json'] = signal_json

# 添加处理时间戳
signal['processing_started_at'] = datetime.now().isoformat()

# 存入processing队列（使用原始JSON）
await redis.zadd(processing_key, {signal_json: time.time()})

# mark_signal_completed()
# ✅ 使用原始JSON删除
signal_json = signal.get('_original_json')
if signal_json is None:
    logger.warning("信号缺少_original_json，使用降级方案")
    signal_json = self._serialize_signal(signal)

result = await redis.zrem(processing_key, signal_json)

if result > 0:
    logger.debug(f"✅ 信号处理完成")
else:
    logger.warning(f"⚠️ 删除失败，可能已被其他进程删除")
```

## 测试验证

### 测试脚本

`scripts/test_signal_deletion.py` - 验证修复效果

### 测试结果

```
======================================================================
✅ 测试通过！信号已成功从processing队列删除
======================================================================

💡 修复验证:
  ✅ _original_json字段正确保存
  ✅ mark_signal_completed()使用原始JSON删除
  ✅ processing队列中的信号被正确清理

  🎉 Bug已彻底修复！
```

## 影响范围

### 修改的文件

1. `src/longport_quant/messaging/signal_queue.py`
   - `consume_signal()` - 保存 `_original_json`
   - `mark_signal_completed()` - 使用原始JSON删除
   - `mark_signal_failed()` - 使用原始JSON删除

### 新增测试

- `scripts/test_signal_deletion.py` - 信号删除功能测试

## 修复前后对比

### 修复前

```
10:02:17 - 队列监控
  待处理队列: 0 个信号
  处理中队列: 7 个信号  ← 卡住的僵尸信号

日志显示:
2025-10-17 10:04:17.431 | DEBUG | ✅ 信号处理完成: 1347.HK
2025-10-17 10:04:15.894 | WARNING | 🔧 恢复僵尸信号: 1347.HK, 已卡住 5.0 分钟
（死循环：处理 → 删除失败 → 恢复 → 重复）
```

### 修复后

```
10:07:45 - 测试结果
  待处理队列: 0 个信号
  处理中队列: 0 个信号  ← 成功清理！

日志显示:
2025-10-17 10:07:45.131 | DEBUG | ✅ 信号处理完成: TEST.HK
（信号正确从processing队列删除，不再重复恢复）
```

## 部署步骤

### 1. 停止所有运行中的进程

```bash
# 停止所有order_executor
pkill -f order_executor

# 停止signal_generator（可选）
pkill -f signal_generator
```

### 2. 清理僵尸信号

```bash
# 清空processing队列
redis-cli DEL trading:signals:processing
```

### 3. 验证修复

```bash
# 运行测试脚本
python3 scripts/test_signal_deletion.py
```

### 4. 重启系统

```bash
# 启动交易系统
bash scripts/start_trading_system.sh
```

## 后续建议

### 监控要点

1. 定期检查 `processing` 队列大小
2. 关注日志中的"删除失败"警告
3. 监控信号恢复频率

### 预防措施

1. **只运行1个 order_executor**（除非需要并发处理）
2. 定期查看队列监控：`python3 scripts/queue_monitor.py`
3. 如果发现僵尸信号，运行：`python3 scripts/recover_stuck_signals.py`

## 相关文档

- `SIGNAL_QUEUE_AUTO_RECOVERY.md` - 自动恢复机制说明
- `scripts/test_signal_deletion.py` - 删除功能测试
- `scripts/test_auto_recovery.py` - 自动恢复测试
- `scripts/queue_monitor.py` - 队列监控工具

## 时间线

- **2025-10-17 09:40** - 用户报告信号卡住问题
- **2025-10-17 09:53** - 实现自动恢复机制
- **2025-10-17 10:02** - 发现自动恢复也无效，信号重复恢复
- **2025-10-17 10:05** - 分析根因：JSON序列化不匹配
- **2025-10-17 10:07** - 修复并测试通过 ✅

## 总结

这是一个**数据序列化导致的关键 bug**：

1. ❌ **症状**：信号永久卡在 processing 队列
2. 🔍 **根因**：修改后的 signal 对象序列化结果与原始 JSON 不匹配
3. ✅ **修复**：保存并使用原始 JSON 进行删除操作
4. 🎯 **效果**：信号正确删除，不再产生僵尸信号

---

**更新时间**: 2025-10-17
**修复者**: Claude Code
**严重程度**: 🔴 严重（影响核心功能）
**状态**: ✅ 已修复并测试通过
