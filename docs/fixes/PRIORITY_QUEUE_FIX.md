# 优先级队列错误修复完成 ✅

## 问题描述
运行时报错：
```
ERROR | signal_processor:397 - 信号处理器错误: '<' not supported between instances of 'dict' and 'dict'
```

## 根本原因
Python的`PriorityQueue`在两个元素具有相同优先级时，会尝试比较队列中的下一个元素（在我们的代码中是字典）。
而Python字典之间不支持直接比较，导致了这个错误。

## 解决方案
在队列元素中添加一个唯一的递增计数器作为"tie-breaker"（决胜因素）：

### 1. 添加信号计数器
```python
# 信号队列（优先级队列）
self.signal_queue = asyncio.PriorityQueue()
self.signal_counter = 0  # 用于生成唯一的信号ID，避免优先级相同时的比较错误
```

### 2. 修改队列元素格式
从二元组 `(priority, data)` 改为三元组 `(priority, counter, data)`：

**买入信号入队：**
```python
# 生成唯一ID避免优先级相同时的字典比较错误
self.signal_counter += 1

# 加入信号队列 (priority, counter, data)
await self.signal_queue.put((
    priority,
    self.signal_counter,  # 新增：唯一计数器
    {
        'symbol': symbol,
        'signal': signal,
        'price': current_price,
        'timestamp': datetime.now()
    }
))
```

**止损止盈信号入队：**
```python
priority = -100  # 止损最高优先级
self.signal_counter += 1
await self.signal_queue.put((
    priority,
    self.signal_counter,  # 新增：唯一计数器
    {
        'symbol': symbol,
        'type': 'STOP_LOSS',
        'position': position,
        'price': current_price,
        'reason': '止损',
        'pnl_pct': pnl_pct
    }
))
```

### 3. 更新信号处理器
```python
async def signal_processor(self):
    while True:
        try:
            # 从优先级队列获取信号 (priority, counter, data)
            priority, counter, signal_data = await self.signal_queue.get()
            # 处理信号...
```

## 测试结果 ✅

### 错误前的表现：
```
2025-10-10 17:47:01.085 | ERROR | signal_processor:397 - 信号处理器错误: '<' not supported between instances of 'dict' and 'dict'
```

### 修复后的表现：
```
2025-10-10 17:49:43.272 | INFO     | signal_processor:362 - 🚀 启动信号处理器...
2025-10-10 17:49:50.484 | INFO     | 账户状态更新: 持仓数: 10/10
✅ 成功订阅 22 个标的的实时行情
```

系统正常运行，没有任何字典比较错误！

## 技术要点

1. **Python PriorityQueue 行为**
   - 使用最小堆实现
   - 当优先级相同时，比较元组的下一个元素
   - 字典不支持比较操作

2. **解决方案模式**
   - 这是一个经典的优先级队列模式
   - 添加唯一递增ID作为第二排序键
   - 保证了稳定的FIFO顺序（相同优先级时）

3. **优先级设计**
   - 止损: -100 (最高优先级)
   - 止盈: -90 (次高优先级)
   - 买入信号: -score (根据评分排序)

## 运行命令

测试模式：
```bash
python scripts/momentum_breakthrough_trading.py --builtin --dry-run --no-slack
```

实盘模式：
```bash
python scripts/momentum_breakthrough_trading.py --builtin --mode HYBRID
```

## 总结

通过添加唯一计数器作为队列元素的第二个排序键，成功解决了优先级相同时的字典比较错误。
系统现在可以正确处理所有信号，包括止损、止盈和买入信号，按照预设的优先级顺序执行。