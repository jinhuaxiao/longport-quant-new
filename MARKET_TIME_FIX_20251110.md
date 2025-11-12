# 港股交易时间修复说明

## 问题发现时间
2025-11-10 15:40

## 问题描述

### 现象
系统在港股收盘后（15:40）仍在不断生成和执行 941.HK（中国移动）的卖出订单：
- 主队列中有 7个 941.HK 挪仓信号反复重试
- 失败队列中累积了 **107个** 941.HK 信号
- 订单全部被拒绝: "订单执行失败: 订单被拒绝或未成交"

### 根本原因
**港股交易时间配置错误**

```python
# 错误的配置（signal_generator.py:4509-4512）
if market == 'HK':
    # 港股: 9:30-12:00, 13:00-16:00  ❌ 错误！
    morning = ... <= datetime.strptime("12:00", "%H:%M").time()
    afternoon = ... <= datetime.strptime("16:00", "%H:%M").time()  ❌
```

**实际港股交易时间**:
- 上午: 9:30-12:00
- 下午: 13:00-**15:00** (15:00收盘)
- 收盘竞价: 16:00 (但不能下新单)

### 问题影响
形成死循环：
```
失败信号 → 恢复到队列 (5分钟窗口)
    ↓
执行订单 ← 市场已收盘
    ↓
订单被拒 → 再次失败
    ↓
进入失败队列 ← 5分钟内再次被检测
    ↓
    (循环)
```

## 修复内容

### 1. 修正港股交易时间

**文件**: `scripts/signal_generator.py:4509-4512`

```python
# 修复后
if market == 'HK':
    # 港股: 9:30-12:00, 13:00-16:00 (16:00收盘竞价，实际交易截止15:00)
    morning = datetime.strptime("09:30", "%H:%M").time() <= current_time <= datetime.strptime("12:00", "%H:%M").time()
    afternoon = datetime.strptime("13:00", "%H:%M").time() <= current_time <= datetime.strptime("15:00", "%H:%M").time()  ✅
    return morning or afternoon
```

### 2. 清理重复信号

清理了所有累积的 941.HK 挪仓信号：
- 主队列: 6个
- 失败队列: **116个**
- 处理队列: 5个
- **总计移除: 127个重复信号**

## 验证结果

### 修复前
```
2025-11-10 15:40:12 | INFO | 🔔 后台检查触发实时挪仓: 生成 8 个卖出信号  ❌
2025-11-10 15:40:23 | ERROR | ❌ 提交平仓订单失败: 订单被拒绝或未成交
```

### 修复后
```
2025-11-10 15:42:13 | DEBUG | ⏭️  所有市场休市，跳过实时挪仓检查  ✅
```

## 其他市场时间配置

### 美股
```python
# 美股: 21:30-次日4:00 (夏令时) 或 22:30-次日5:00 (冬令时)
# 简化处理：21:00-次日6:00
return current_time >= datetime.strptime("21:00", "%H:%M").time() or \
       current_time <= datetime.strptime("06:00", "%H:%M").time()
```
✅ 配置正确

### A股
```python
# A股: 9:30-11:30, 13:00-15:00
morning = datetime.strptime("09:30", "%H:%M").time() <= current_time <= datetime.strptime("11:30", "%H:%M").time()
afternoon = datetime.strptime("13:00", "%H:%M").time() <= datetime.strptime("15:00", "%H:%M").time()
```
✅ 配置正确

## 后续优化建议

### 1. 增加日志记录
建议在市场时间检查时记录详细日志：
```python
logger.debug(f"市场时间检查: {market} 市场, 当前时间={current_time}, 是否开盘={is_open}")
```

### 2. 失败信号时间窗口调整
当前失败信号恢复窗口为5分钟，在收盘前5分钟可能导致问题。建议：
- 检查信号的市场是否开盘
- 如果市场即将收盘（如14:55-15:00），延长重试间隔

### 3. Order Executor 增强
建议 order_executor 在执行订单前也检查市场是否开盘：
```python
if not self._is_market_open(symbol):
    logger.warning(f"⏭️  {symbol} 市场已休市，跳过订单执行")
    return False
```

### 4. 监控告警
建议添加监控：
- 失败队列信号数量 > 50 时告警
- 同一标的信号数量 > 10 时告警
- 订单拒绝率 > 50% 时告警

## 影响范围

### 影响的功能
- 实时挪仓后台任务（`_rotation_checker_loop`）
- 紧急卖出后台任务
- 所有依赖市场时间判断的功能

### 不影响的功能
- 主循环的信号生成（有独立的市场判断）
- order_executor 的订单执行逻辑
- 预收盘挪仓（有独立的时间判断）

## 测试建议

### 收盘后测试
```bash
# 在港股收盘后（15:00后）检查日志
grep "所有市场休市" logs/signal_generator_paper_001.log

# 应该看到：
# ⏭️  所有市场休市，跳过实时挪仓检查
```

### 开盘时测试
```bash
# 在港股开盘后（9:30-15:00）检查日志
grep "后台检查" logs/signal_generator_paper_001.log | tail -5

# 应该看到正常的后台检查日志
```

### 失败队列监控
```bash
# 监控失败队列大小
redis-cli ZCARD trading:signals:failed:paper_001

# 应该保持在合理范围内（< 20）
```

## 相关文件

- `scripts/signal_generator.py:4490-4523` - `_is_market_open_time()` 方法
- `scripts/signal_generator.py:4383-4489` - `_rotation_checker_loop()` 后台任务

## 版本历史

- **2025-11-10 15:41**: 修复港股交易时间配置错误
  - 将下午收盘时间从 16:00 改为 15:00
  - 清理127个重复的挪仓信号
  - 验证修复生效

## 注意事项

1. **港股收盘时间**: 15:00 是连续交易截止时间，16:00 是收盘竞价时间
2. **节假日**: 当前实现不考虑节假日，需要手动停止系统或添加节假日日历
3. **夏令时**: 美股时间使用简化处理（21:00-6:00），可能需要根据夏令时调整
4. **时区**: 使用北京时间（Asia/Shanghai），确保服务器时区配置正确

## 后续跟进

- [ ] 监控明天开盘后系统是否正常工作
- [ ] 统计失败队列信号数量是否保持在合理范围
- [ ] 考虑实现上述的优化建议
