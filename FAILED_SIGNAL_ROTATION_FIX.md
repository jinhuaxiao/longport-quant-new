# 失败信号实时挪仓修复说明

## 问题背景

之前的实时挪仓功能存在设计缺陷：
- 只检查**延迟信号**（有 `retry_after` 字段的信号）
- 不检查**失败信号**（直接进入失败队列的信号）
- 导致因资金不足而无法买入一手的高分信号被忽略，无法触发挪仓

### 典型场景

```
1. 高分信号生成 (例如: 386.HK 66分)
2. order_executor 尝试执行
3. 发现资金不足，连一手都买不起
4. 信号被直接移到失败队列
5. ❌ 实时挪仓检查失败队列 -> 不触发挪仓
6. 高分信号永远无法执行
```

## 修复内容

### 1. SignalQueue 新增方法

**文件**: `src/longport_quant/messaging/signal_queue.py`

#### get_failed_signals()
获取失败队列中的高分信号（5分钟内）

```python
async def get_failed_signals(
    self,
    account: Optional[str] = None,
    min_score: int = 60,
    max_age_seconds: int = 300  # 5分钟时间窗口
) -> List[Dict]:
    """
    获取失败队列中因资金不足而失败的高分信号
    """
```

**位置**: `signal_queue.py:836-891`

#### recover_failed_signal()
从失败队列恢复信号到主队列

```python
async def recover_failed_signal(self, signal: Dict) -> bool:
    """
    从失败队列恢复信号到主队列
    清理失败相关字段，重置重试计数
    """
```

**位置**: `signal_queue.py:893-935`

### 2. 实时挪仓逻辑增强

**文件**: `scripts/signal_generator.py`

#### 检查延迟和失败信号

```python
# 1. 获取延迟信号列表（重试队列）
delayed_signals = await self.signal_queue.get_delayed_signals(
    account=self.settings.account_id
)

# 2. 获取失败信号列表（失败队列中5分钟内的高分信号）
failed_signals = await self.signal_queue.get_failed_signals(
    account=self.settings.account_id,
    min_score=getattr(self.settings, 'realtime_rotation_min_signal_score', 60),
    max_age_seconds=300
)

# 合并延迟信号和失败信号
all_pending_signals = delayed_signals + failed_signals
```

**位置**: `signal_generator.py:3915-3930`

#### 恢复失败信号

挪仓成功后，自动恢复失败队列中的高分信号到主队列：

```python
# 如果生成了挪仓信号，尝试恢复失败队列中的高分信号
if rotation_signals and hasattr(self, '_pending_rotation_signals'):
    recovered_count = 0
    for signal in self._pending_rotation_signals:
        # 只恢复来自失败队列的信号
        if 'failed_at' in signal:
            success = await self.signal_queue.recover_failed_signal(signal)
            if success:
                recovered_count += 1

    if recovered_count > 0:
        logger.success(f"✅ 已恢复 {recovered_count} 个失败信号到队列，等待重新执行")
```

**位置**: `signal_generator.py:4211-4225`

### 3. 修复的Bug

#### Bug 1: 变量名错误
**错误**: `high_score_delayed` 未定义
**原因**: 变量名为 `high_score_pending` 但被引用为 `high_score_delayed`
**修复**: 统一使用 `high_score_pending`
**位置**: `signal_generator.py:3994`

#### Bug 2: 类型不兼容
**错误**: `unsupported operand type(s) for *: 'float' and 'decimal.Decimal'`
**原因**: 持仓数据中的 `avg_cost` 和 `quantity` 是 Decimal 类型
**修复**: 添加 `float()` 类型转换
**位置**: `signal_generator.py:4037-4038`

```python
cost_price = float(pos.get('avg_cost', current_price))
quantity = float(pos.get('quantity', 0))
```

#### Bug 3: 方法不存在
**错误**: `'SignalGenerator' object has no attribute '_publish_signal_to_queue'`
**原因**: 使用了不存在的方法名
**修复**: 改为正确的方法 `self.signal_queue.publish_signal()`
**位置**: `signal_generator.py:4459, 4473`

## 工作流程

### 修复前
```
延迟信号 -> 实时挪仓检查 -> 触发挪仓
失败信号 -> ❌ 被忽略
```

### 修复后
```
延迟信号 ┐
         ├-> 合并 -> 实时挪仓检查 -> 触发挪仓 -> 恢复失败信号
失败信号 ┘
```

### 详细流程

1. **后台任务检查**（每30秒）
   ```
   _rotation_checker_loop()
   └─> check_realtime_rotation()
       ├─> get_delayed_signals()    # 获取延迟信号
       ├─> get_failed_signals()     # 获取失败信号
       ├─> 合并并筛选高分买入信号
       ├─> 分析持仓质量
       ├─> 生成挪仓卖出信号
       └─> recover_failed_signal()  # 恢复失败信号
   ```

2. **信号恢复**
   ```
   失败队列 -> recover_failed_signal()
             ├─> 从失败队列移除
             ├─> 清理失败字段
             ├─> 重置重试计数
             └─> 重新发布到主队列
   ```

3. **order_executor 处理**
   ```
   主队列 -> 获取信号 -> 执行订单
   ```

## 测试结果

### 测试脚本
`test_failed_signal_detection.py` - 创建失败信号并验证系统能否检测和恢复

### 测试输出
```
1️⃣ 创建测试失败信号...
   ✅ 测试信号已添加到失败队列 (TEST.HK, 评分=75)

2️⃣ 等待后台任务检测（最多35秒）...
   ⏳ 等待30秒...

3️⃣ 检查最终状态...
   ✅ 主队列: 已恢复
   ✅ 处理队列: 正在处理
```

### 日志验证
```
2025-11-10 15:25:27 | INFO  | 🔔 检测到 2 个高分信号因资金不足无法买入 (延迟重试: 1, 已失败: 2)
2025-11-10 15:25:27 | INFO  | 🎯 分析高分延迟信号: TEST.HK (评分=75)
2025-11-10 15:25:34 | INFO  | ✅ 信号已从失败队列恢复: TEST.HK, 评分=75
2025-11-10 15:25:34 | SUCCESS | ✅ 已恢复 2 个失败信号到队列，等待重新执行
2025-11-10 15:25:34 | INFO  | 🔔 后台检查触发实时挪仓: 生成 2 个卖出信号
```

## 配置参数

### 失败信号时间窗口
当前硬编码为 5 分钟（300秒），只考虑5分钟内失败的信号：

```python
failed_signals = await self.signal_queue.get_failed_signals(
    account=self.settings.account_id,
    min_score=60,
    max_age_seconds=300  # 5分钟
)
```

**原因**: 避免反复尝试过时的失败信号

### 最低分数要求
使用配置文件中的 `realtime_rotation_min_signal_score` 参数（默认60分）

## 影响范围

### 新增代码
- `SignalQueue.get_failed_signals()` - 58行
- `SignalQueue.recover_failed_signal()` - 43行
- 实时挪仓逻辑增强 - 20行

### 修改代码
- `check_realtime_rotation()` - 信号获取逻辑
- `_rotation_checker_loop()` - 发布方法修正

### 不影响
- 延迟信号的原有逻辑
- 预收盘挪仓逻辑
- 紧急卖出逻辑
- order_executor 执行逻辑

## 监控建议

### 日志关键字
- `检测到.*高分信号因资金不足`
- `已失败: \d+` （失败信号数量）
- `已恢复.*失败信号`
- `后台检查触发实时挪仓`

### Redis 监控
```bash
# 查看失败队列大小
redis-cli ZCARD trading:signals:failed:paper_001

# 查看失败队列中的高分信号
redis-cli ZRANGE trading:signals:failed:paper_001 0 -1
```

### 日志监控
```bash
# 监控失败信号检测
tail -f logs/signal_generator_paper_001.log | grep "已失败"

# 监控信号恢复
tail -f logs/signal_generator_paper_001.log | grep "恢复.*失败信号"
```

## 预期效果

### 修复前
- 高分信号因资金不足失败后，永远不会被重新尝试
- 即使有可挪仓的低质量持仓，也不会触发挪仓

### 修复后
- 后台任务每30秒检查失败队列（5分钟窗口）
- 检测到高分失败信号时，触发实时挪仓
- 挪仓成功后，自动恢复失败信号到主队列
- 信号有机会重新执行

## 注意事项

1. **时间窗口**: 只考虑5分钟内失败的信号，防止反复尝试过时信号
2. **账号隔离**: 每个账号有独立的失败队列
3. **分数门槛**: 只恢复评分 >= 60 的信号（可配置）
4. **重复恢复**: 如果信号再次失败，会再次进入失败队列，5分钟内仍有机会被恢复
5. **市场时间**: 只在市场交易时间内检查（HK/US开盘时）

## 版本历史

- **2025-11-10**: 初始修复完成
  - 新增失败信号检测功能
  - 新增信号恢复功能
  - 修复3个bug（变量名、类型兼容、方法名）
  - 测试验证通过
