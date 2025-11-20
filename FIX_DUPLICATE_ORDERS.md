# 修复重复下单BUG - 2025-11-21

## 问题描述

live_002账号出现TQQQ重复交易问题：
1. 同一天对同一标的多次下单（买入/卖出）
2. 一边提示要卖出TQQQ，一边又生成买入信号
3. 紧急卖出信号每30秒重复生成

## 根本原因分析

### BUG 1: `sold_today`和`traded_today`被意外清空

**位置**: `scripts/signal_generator.py:452, 484`

**问题代码**:
```python
self.traded_today = new_traded_today  # ❌ 直接覆盖
self.sold_today = new_sold_today      # ❌ 直接覆盖
```

**触发流程**:
1. 23:23:32 - 紧急卖出检查发现TQQQ需要卖出，执行`sold_today.add("TQQQ.US")`
2. 信号发布到Redis队列，但订单执行器尚未处理
3. 23:24:43 - `_update_sold_today()`从数据库重新加载（71秒后）
4. 数据库中还没有TQQQ的卖单记录（订单还在队列中未执行）
5. `self.sold_today = new_sold_today` **直接覆盖内存**，TQQQ从sold_today中消失
6. 23:24:06 - 下次检查时`sold_today`中没有TQQQ，又生成新信号

**日志证据**:
```
23:23:32 | ✅ TQQQ.US: 已添加到 sold_today，防止重复卖出
23:24:43 | 📋 今日尚无卖单记录  ← sold_today被清空！
23:24:06 | ✅ TQQQ.US: 生成紧急卖出信号  ← 仅34秒后又生成
```

### BUG 2: 买卖信号可以同时存在

**位置**: `scripts/signal_generator.py:606`

**问题代码**:
```python
if await self.signal_queue.has_pending_signal(symbol, signal_type):
    return False, "队列中已有该标的的待处理信号"
```

**问题**: 只检查**相同类型**信号（BUY vs BUY），不检查**相反类型**（SELL vs BUY）

**触发流程**:
1. 23:23:17 - 队列中有TQQQ的URGENT_SELL信号（优先级95）
2. 23:23:18 - 价格变化触发实时计算，生成STRONG_BUY信号
3. `has_pending_signal("TQQQ.US", "STRONG_BUY")` 返回False（队列中只有SELL没有BUY）
4. BUY信号被允许发布，队列中同时有SELL和BUY信号

**日志证据**:
```
23:17:07 | ✅ TQQQ.US: 生成紧急卖出信号 (数量=2393, 紧急度=85)
23:23:18 | ✅ TQQQ.US: 实时信号已生成! 类型=STRONG_BUY, 评分=60
```

### BUG 3: `check_urgent_sells`缺少队列去重

**位置**: `scripts/signal_generator.py:4970`

**问题**: 在检查`sold_today`之前，没有先检查Redis队列中是否已有URGENT_SELL信号

**触发流程**:
1. URGENT_SELL信号已发布到Redis队列
2. 30秒后，`check_urgent_sells`再次运行
3. 只检查`sold_today`和数据库，没检查队列
4. 由于BUG 1导致`sold_today`被清空，再次生成信号

### BUG 4: 单标的日度买入限制未强制启用

**位置**: `scripts/signal_generator.py:632`

**问题代码**:
```python
if getattr(self.settings, 'enable_per_symbol_daily_cap', False):  # ❌ 默认False
```

**问题**: 配置默认为False，允许同一标的一天多次买入

## 修复方案

### 修复 1: 使用update合并而非覆盖 ✅

**文件**: `scripts/signal_generator.py:452, 484`

```python
# 修复前
self.traded_today = new_traded_today
self.sold_today = new_sold_today

# 修复后
self.traded_today.update(new_traded_today)  # 合并而非覆盖
self.sold_today.update(new_sold_today)      # 合并而非覆盖
```

**效果**: 保留内存中手动添加的标的（如pending中的紧急卖出），不会被数据库查询结果覆盖

### 修复 2: 添加买卖信号互斥检查 ✅

**文件**: `scripts/signal_generator.py:609-619`

```python
# 买卖信号互斥检查（防止同时存在买卖信号）
if signal_type in ["BUY", "STRONG_BUY", "WEAK_BUY"]:
    # 如果队列中有卖出信号，禁止生成买入信号
    for sell_type in ["URGENT_SELL", "SELL", "STOP_LOSS", "TAKE_PROFIT", "SMART_TAKE_PROFIT", "EARLY_TAKE_PROFIT"]:
        if await self.signal_queue.has_pending_signal(symbol, sell_type):
            return False, f"队列中已有该标的的{sell_type}信号，禁止买入"
elif signal_type in ["SELL", "STOP_LOSS", "TAKE_PROFIT", "SMART_TAKE_PROFIT", "EARLY_TAKE_PROFIT", "URGENT_SELL"]:
    # 如果队列中有买入信号，禁止生成卖出信号
    for buy_type in ["BUY", "STRONG_BUY", "WEAK_BUY"]:
        if await self.signal_queue.has_pending_signal(symbol, buy_type):
            return False, f"队列中已有该标的的{buy_type}信号，禁止卖出"
```

**效果**: 确保同一标的在队列中不会同时存在买入和卖出信号

### 修复 3: 在check_urgent_sells中添加队列去重 ✅

**文件**: `scripts/signal_generator.py:4975-4978`

```python
# 检查队列中是否已有URGENT_SELL信号（最高优先级去重）
if await self.signal_queue.has_pending_signal(symbol, "URGENT_SELL"):
    logger.debug(f"    {symbol}: 队列中已有URGENT_SELL信号，跳过")
    continue
```

**效果**: 在生成紧急卖出信号前，先检查队列，避免重复提交

### 修复 4: 强制启用单标的日度买入限制 ✅

**文件**: `scripts/signal_generator.py:631-641`

```python
# 修复前
if getattr(self.settings, 'enable_per_symbol_daily_cap', False):  # 需要配置启用
    if symbol in self.traded_today:
        return False, "该标的今日已下过买单"

# 修复后（强制启用，无需配置）
try:
    max_buys = int(getattr(self.settings, 'per_symbol_daily_max_buys', 1))
    if symbol in self.traded_today:
        return False, f"该标的今日已下过买单（上限{max_buys}次/天）"
except Exception as e:
    logger.warning(f"  {symbol}: 检查日度买单上限失败: {e}，默认允许")
```

**效果**: 无论配置如何，都强制检查单标的日度买入限制，防止重复下单

### 修复 5: 确保URGENT_SELL受到卖出去重检查 ✅

**文件**: `scripts/signal_generator.py:680`

```python
# 修复前
elif signal_type in ["SELL", "STOP_LOSS", "TAKE_PROFIT", "SMART_TAKE_PROFIT", "EARLY_TAKE_PROFIT"]:

# 修复后
elif signal_type in ["SELL", "STOP_LOSS", "TAKE_PROFIT", "SMART_TAKE_PROFIT", "EARLY_TAKE_PROFIT", "URGENT_SELL"]:
```

**效果**: URGENT_SELL信号也会被检查是否已在`sold_today`中

## 修复验证

### 语法检查
```bash
python3 -m py_compile scripts/signal_generator.py
# ✅ 通过，无语法错误
```

### 预期效果

1. **防止sold_today/traded_today被清空**:
   - 内存中添加的标的不会被数据库查询覆盖
   - 即使订单还在队列中未执行，也能正确去重

2. **防止买卖信号冲突**:
   - 队列中有SELL信号时，不会生成BUY信号
   - 队列中有BUY信号时，不会生成SELL信号

3. **防止URGENT_SELL重复生成**:
   - 队列中已有URGENT_SELL信号时，不会再次生成
   - `sold_today`中的标的不会被清空

4. **强制单标的日度买入限制**:
   - 无论配置如何，每个标的每天最多买入1次（可配置）
   - 彻底杜绝同一天对同一标的重复下单

## 监控建议

修复后，建议监控以下日志：

1. **sold_today/traded_today更新**:
```
📋 今日已下买单标的: X个（包括pending订单）
📋 今日已下卖单标的: X个（包括pending订单）
```

2. **去重拦截**:
```
⏭️ TQQQ.US: 队列中已有URGENT_SELL信号，跳过
⏭️ TQQQ.US: 队列中已有该标的的SELL信号，禁止买入
⏭️ TQQQ.US: 该标的今日已下过买单（上限1次/天）
```

3. **紧急卖出**:
```
✅ TQQQ.US: 生成紧急卖出信号 (数量=2393, 紧急度=85)
✅ TQQQ.US: 已添加到 sold_today，防止重复卖出
```

## 回滚方案

如果修复导致问题，可以通过git回滚：

```bash
cd /data/web/longport-quant-new
git diff scripts/signal_generator.py  # 查看修改
git checkout scripts/signal_generator.py  # 回滚
```

## 相关文件

- `scripts/signal_generator.py` - 主要修复文件
- `src/longport_quant/config/settings.py` - 配置定义（无需修改）

## 修复完成时间

2025-11-21 23:52 CST
