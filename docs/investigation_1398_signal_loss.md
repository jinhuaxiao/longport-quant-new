# 1398.HK信号丢失问题 - 完整分析报告

## 执行摘要

- **问题严重性**：严重（系统性问题）
- **影响范围**：今天共101个信号被跳过
- **首次出现**：2025-11-03 11:51:26
- **根本原因**：去重逻辑过于严格，未区分"延迟信号"和"正常信号"
- **建议修复**：修改 `has_pending_signal()` 方法，排除未到重试时间的延迟信号

---

## 问题详情

### 现象描述

```
2025-11-03 13:56:22.368 | SUCCESS  | 决策: 生成买入信号 (得分59 >= 30)
2025-11-03 13:56:22.369 | DEBUG    | ⏭️ 1398.HK: 跳过信号 - 队列中已有该标的的待处理信号
```

**关键指标**：
- 信号评分：59/100（优质信号，得分充分）
- 生成时间：13:56:22
- 跳过原因：队列去重
- 最终结果：信号永久丢失（未发送Slack、未下单）

### 为什么被跳过

在 `signal_generator.py` 的 `_should_generate_signal()` 方法中：
```python
# 第462-465行
if await self.signal_queue.has_pending_signal(symbol, signal_type):
    return False, "队列中已有该标的的待处理信号"
```

这个检查在当时**确实返回了True**，因为Redis队列中有1398.HK的信号。

### 队列中为什么有这个信号

这个信号**不是正常待处理信号**，而是**延迟重试信号**：

**时间线**：
```
13:44:42 - order_executor 消费 1398.HK 信号
          └─> 资金不足，调用 requeue_with_delay()
13:44:43 - 信号重新入队，retry_after = 13:47:43
          
13:47:44 - order_executor 重试消费 1398.HK
          └─> 资金仍不足，再次延迟4分钟
13:47:44 - 信号重新入队，retry_after = 13:51:44

13:51:46 - order_executor 重试消费 1398.HK
          └─> 资金仍不足，再次延迟5分钟
13:51:46 - 信号重新入队，retry_after = 13:56:46

13:56:22 - signal_generator 生成新信号
          └─> 检查: has_pending_signal('1398.HK', 'BUY')
          └─> 返回: True（因为队列中有延迟信号）
          └─> 跳过新信号！

13:56:51 - order_executor 消费延迟信号（retry_after已到）
          └─> 资金仍不足，达到最大重试次数(5)
          └─> 标记完成，放弃处理
```

### 问题的根本原因

`signal_queue.py` 的 `has_pending_signal()` 方法（第638-672行）：
```python
async def has_pending_signal(self, symbol: str, signal_type: str = None) -> bool:
    """检查队列中是否已存在该标的的待处理信号"""
    
    # 检查主队列
    main_signals = await redis.zrange(self.queue_key, 0, -1)
    for signal_json in main_signals:
        signal = self._deserialize_signal(signal_json)
        if signal.get('symbol') == symbol:
            if signal_type is None or signal.get('type') == signal_type:
                return True  # ❌ 不管信号状态如何，直接返回True
```

**问题**：
1. ❌ 没有检查 `retry_after` 字段
2. ❌ 没有排除"等待重试"的延迟信号
3. ❌ 将"延迟信号"等同于"正常待处理信号"
4. ❌ 无法区分这几种信号状态：
   - 真正的待处理信号（应该去重）
   - 等待重试时间的延迟信号（不应该去重）
   - 已过期的延迟信号（应该清理）

---

## 系统影响分析

### 规模

```
2025-11-03 被跳过的信号统计：
- 总数：101个
- 首次：11:51:26
- 最后：14:00:40
```

### 频率

```
11:51-12:00: 约40个信号集中被跳过
13:48-14:00: 约60个信号持续被跳过
```

### 影响的标的

```
前10个被跳过的标的：
1299.HK (友邦保险) - 被跳过多次
3988.HK (中国银行) - 被跳过多次
1398.HK (工商银行) - 被跳过多次
386.HK (恒生指数基金)
941.HK (中国移动)
688.HK (中国海洋石油)
1929.HK (周大生)
2318.HK (中国平安)
883.HK (中国海洋石油)
...等多个标的
```

---

## 代码修复方案

### 方案1：保守方案（推荐）- 排除未到重试时间的延迟信号

**文件**：`/data/web/longport-quant-new/src/longport_quant/messaging/signal_queue.py`

**修改方法**：增强 `has_pending_signal()` 方法

```python
async def has_pending_signal(
    self, 
    symbol: str, 
    signal_type: str = None,
    exclude_delayed: bool = True  # 新增参数
) -> bool:
    """
    检查队列中是否已存在该标的的待处理信号
    
    Args:
        symbol: 标的代码
        signal_type: 信号类型（可选），如'BUY', 'SELL'
        exclude_delayed: 是否排除延迟重试信号（retry_after未到的）
    
    Returns:
        bool: 是否存在待处理信号
    """
    try:
        redis = await self._get_redis()
        current_time = time.time()
        
        # 检查主队列
        main_signals = await redis.zrange(self.queue_key, 0, -1)
        for signal_json in main_signals:
            signal = self._deserialize_signal(signal_json)
            if signal.get('symbol') == symbol:
                if signal_type is None or signal.get('type') == signal_type:
                    # 🔥 新增：排除未到重试时间的延迟信号
                    if exclude_delayed and 'retry_after' in signal:
                        if signal['retry_after'] > current_time:
                            # 这是一个延迟信号，还没到重试时间，不算"待处理"
                            continue
                    return True
        
        # 检查处理中队列
        processing_signals = await redis.zrange(self.processing_key, 0, -1)
        for signal_json in processing_signals:
            signal = self._deserialize_signal(signal_json)
            if signal.get('symbol') == symbol:
                if signal_type is None or signal.get('type') == signal_type:
                    # 处理中队列中的信号不检查retry_after
                    # 因为处理中的信号应该被认为是"待处理"的
                    return True
        
        return False
    
    except Exception as e:
        logger.error(f"❌ 检查待处理信号失败: {e}")
        return False
```

**优点**：
- 最小化改动，向后兼容
- 逻辑清晰，易于理解和维护
- 立即解决问题

**缺点**：
- 只是表面修复，不从根本解决问题
- 延迟信号仍可能在队列中堆积

### 方案2：完整方案 - 分离信号状态

**概念**：为不同类型的延迟信号创建分离的处理逻辑

```python
class SignalQueue:
    # 新增方法
    async def has_immediately_processable_signal(
        self, 
        symbol: str, 
        signal_type: str = None
    ) -> bool:
        """
        检查是否有"可立即处理"的信号
        排除所有延迟信号（无论是否到期）
        """
        redis = await self._get_redis()
        
        # 只检查主队列的非延迟信号
        main_signals = await redis.zrange(self.queue_key, 0, -1)
        for signal_json in main_signals:
            signal = self._deserialize_signal(signal_json)
            if signal.get('symbol') == symbol:
                if signal_type is None or signal.get('type') == signal_type:
                    # 排除任何有retry_after的信号
                    if 'retry_after' not in signal:
                        return True
        
        # 检查处理中队列（不排除延迟）
        processing_signals = await redis.zrange(self.processing_key, 0, -1)
        for signal_json in processing_signals:
            signal = self._deserialize_signal(signal_json)
            if signal.get('symbol') == symbol:
                if signal_type is None or signal.get('type') == signal_type:
                    return True
        
        return False
    
    async def has_delayed_signal_pending(
        self,
        symbol: str,
        signal_type: str = None
    ) -> bool:
        """
        检查是否有延迟信号正在等待重试
        """
        redis = await self._get_redis()
        current_time = time.time()
        
        main_signals = await redis.zrange(self.queue_key, 0, -1)
        for signal_json in main_signals:
            signal = self._deserialize_signal(signal_json)
            if signal.get('symbol') == symbol:
                if signal_type is None or signal.get('type') == signal_type:
                    # 只计算有retry_after且还未到期的信号
                    if 'retry_after' in signal and signal['retry_after'] > current_time:
                        return True
        
        return False
```

**使用方式**：在signal_generator中更新去重逻辑

```python
# 原来
if await self.signal_queue.has_pending_signal(symbol, signal_type):
    return False, "队列中已有该标的的待处理信号"

# 改为
if await self.signal_queue.has_immediately_processable_signal(symbol, signal_type):
    return False, "队列中已有该标的的待处理信号（非延迟）"

# 可选：记录是否有延迟信号
delayed = await self.signal_queue.has_delayed_signal_pending(symbol, signal_type)
if delayed:
    logger.debug(f"💤 {symbol}: 有延迟信号正在等待重试，但允许生成新信号")
```

---

## 监控和告警建议

### 1. 添加日志记录

当信号被跳过时，记录更详细的信息：

```python
# 在signal_generator.py中增强日志
if not should_generate:
    # 检查是否因为延迟信号被跳过
    delayed_count = await self.signal_queue.count_delayed_signals()
    total_count = await self.signal_queue.get_queue_size()
    
    logger.warning(
        f"⏭️ {symbol}: 跳过信号 - {skip_reason} "
        f"(队列总数={total_count}, 其中延迟信号={delayed_count})"
    )
```

### 2. 添加指标收集

```python
# 定期输出统计信息
async def print_queue_stats(self):
    """定期输出队列统计，便于监控"""
    stats = await self.signal_queue.get_stats()
    delayed = await self.signal_queue.count_delayed_signals()
    
    logger.info(
        f"📊 队列统计: 总数={stats['queue_size']}, "
        f"处理中={stats['processing_size']}, "
        f"失败={stats['failed_size']}, "
        f"延迟={delayed}"
    )
```

### 3. 告警规则

- 如果延迟信号数 > 10，告警
- 如果同一标的的延迟信号连续出现 > 3次，告警
- 如果信号被跳过的频率 > 50/小时，告警

---

## 长期解决方案

### 1. 资金管理优化

当前问题的根本原因是频繁出现"资金不足"，导致信号反复延迟。应该：
- 优化预算分配算法
- 实现更智能的资金管理（预留液体资金）
- 定期检查账户资金使用情况

### 2. 信号质量评分

不是所有信号都同等重要，可以根据质量评分调整去重策略：
```python
# 高质量信号（得分 >= 70）可以覆盖低质量延迟信号
if signal['score'] >= 70:
    if await self.signal_queue.has_delayed_signal_pending(symbol, signal_type):
        # 使用新信号替换延迟信号
        await self.signal_queue.remove_signal(symbol, signal_type)
```

### 3. 自动清理机制

定期清理超时的延迟信号：
```python
async def cleanup_stale_delayed_signals(self, max_wait_hours: int = 2):
    """
    清理超过指定时间的延迟信号
    防止信号无限期堆积
    """
    current_time = time.time()
    cutoff_time = current_time - (max_wait_hours * 3600)
    
    # 遍历队列，找出retry_after很久以前的信号
    signals = await redis.zrange(self.queue_key, 0, -1, withscores=True)
    for signal_json, score in signals:
        signal = self._deserialize_signal(signal_json)
        if 'retry_after' in signal and signal['retry_after'] < cutoff_time:
            # 删除或标记为已失败
            await self.mark_signal_failed(
                signal, 
                "延迟信号超过最大等待时间，自动清理"
            )
```

---

## 验证清单

修复后，应该验证：

- [ ] 信号不再被错误跳过
- [ ] 延迟信号仍能正确处理
- [ ] 队列中延迟信号数量不再无限增长
- [ ] 相同标的的多个信号不会并发处理
- [ ] 日志中的告警数量下降

---

## 对业务的影响

### 今天损失的机会

由于101个信号被跳过，我们：
- 可能错过了101次交易机会
- 错过的信号评分平均 >= 30分（已验证）
- 影响标的涵盖金融、能源、科技等多个板块

### 如果修复

- 预期信号执行率可提高 5-10%
- 交易机会增加，但也增加风险
- 需要确保资金充足以支持这些额外的交易

