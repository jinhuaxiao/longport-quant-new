# 信号队列优化 - 资金不足重试机制改进

## 📋 优化背景

**用户问题**：
```
2025-11-03 11:13:05.082 | DEBUG | 批次模式: 队列有10个信号，使用批次收集（窗口=15.0秒）
2025-11-03 11:13:05.088 | DEBUG | ⏰ 队列中所有信号(10个)都未到重试时间，暂无可处理信号
```

**问题分析**：
1. 延迟时间过长（2分钟）导致信号处理缓慢
2. 一个信号资金不足会导致整个批次的所有信号都被延迟（级联延迟）
3. 卖出后释放资金，但延迟信号仍在等待，不能立即处理

## ✅ 已完成的优化

### 1. 缩短重试延迟时间

**文件**: `configs/accounts/paper_001.env`

```bash
# 修改前
FUNDS_RETRY_DELAY=2          # 资金不足时重试延迟（分钟）
FUNDS_RETRY_MAX=3            # 最大重试次数

# 修改后
FUNDS_RETRY_DELAY=1          # 优化：缩短至1分钟
FUNDS_RETRY_MAX=5            # 优化：增加至5次
```

**效果**：
- 重试延迟从2分钟缩短至1分钟（减少50%等待时间）
- 重试次数从3次增加至5次（提高信号成功率）

### 2. 修复批量延迟级联问题

**文件**: `scripts/order_executor.py`

**修改位置**: 第250-262行

**修改前（问题代码）**：
```python
except InsufficientFundsError as e:
    # 资金不足：停止处理当前批次，剩余信号延迟重试
    logger.warning(f"  ⚠️ [{idx}/{len(batch)}] {symbol}: 资金不足")
    logger.info(f"  💡 策略：将剩余{len(batch)-idx}个信号延迟重试")

    # 当前信号也加入待重新入队列表
    remaining_signals.append(signal)

    # 【问题】将后续所有信号也加入待重新入队列表
    remaining_signals.extend(batch[idx:])

    funds_exhausted = True
    break  # 跳出循环，不再处理本批次剩余信号
```

**修改后（优化代码）**：
```python
except InsufficientFundsError as e:
    # 资金不足：只延迟当前信号，继续处理后续信号（可能需要更少资金）
    logger.warning(f"  ⚠️ [{idx}/{len(batch)}] {symbol}: 资金不足")
    logger.info(f"  💡 策略：仅延迟当前信号，继续处理后续{len(batch)-idx}个信号")

    # 只将当前信号加入待重新入队列表
    remaining_signals.append(signal)

    # 标记此信号为资金不足（用于统计）
    funds_exhausted = True
    # 不break，继续处理后续信号
```

**核心改进**：
- 移除了 `remaining_signals.extend(batch[idx:])` 这一行（导致级联延迟的根源）
- 移除了 `break` 语句，允许继续处理后续信号
- 只延迟资金不足的单个信号，其他信号继续处理

**效果**：
- 10个信号中第3个资金不足 → 只延迟第3个，第4-10个继续处理
- 避免"一人延迟，全队等待"的问题

### 3. 添加资金监控触发机制

#### 3.1 SignalQueue 新增方法

**文件**: `src/longport_quant/messaging/signal_queue.py`

**新增方法1**: `count_delayed_signals()` (第703-734行)
```python
async def count_delayed_signals(self, account: Optional[str] = None) -> int:
    """
    统计队列中延迟重试的信号数量

    Args:
        account: 账号ID（可选），如果指定则只统计该账号的信号

    Returns:
        int: 延迟信号数量
    """
```

**新增方法2**: `wake_up_delayed_signals()` (第736-789行)
```python
async def wake_up_delayed_signals(self, account: Optional[str] = None) -> int:
    """
    唤醒延迟重试的信号（移除retry_after字段）

    当资金充足时调用此方法，让延迟的信号立即可被处理

    Args:
        account: 账号ID（可选），如果指定则只唤醒该账号的信号

    Returns:
        int: 被唤醒的信号数量
    """
```

**功能说明**：
- `count_delayed_signals`: 统计有多少信号因资金不足被延迟
- `wake_up_delayed_signals`: 移除信号的 `retry_after` 时间戳，使其立即可被处理

#### 3.2 OrderExecutor 新增监控方法

**文件**: `scripts/order_executor.py`

**新增方法**: `_check_delayed_signals()` (第2137-2167行)
```python
async def _check_delayed_signals(self):
    """
    检查并唤醒延迟信号（卖出后资金可能充足）

    应在卖出订单完成后调用，让因资金不足延迟的信号立即可被处理
    """
    try:
        # 统计延迟信号数量
        delayed_count = await self.signal_queue.count_delayed_signals(
            account=self.settings.account_id
        )

        if delayed_count > 0:
            logger.info(
                f"💰 卖出后资金释放，检测到{delayed_count}个延迟信号，尝试唤醒..."
            )

            # 唤醒延迟信号
            woken_count = await self.signal_queue.wake_up_delayed_signals(
                account=self.settings.account_id
            )

            if woken_count > 0:
                logger.success(
                    f"✅ 已唤醒{woken_count}个延迟信号，将在下次循环中处理"
                )
    except Exception as e:
        logger.warning(f"⚠️ 检查延迟信号失败（不影响主流程）: {e}")
```

**触发时机**: 在 `_execute_sell_order()` 方法中，卖出订单完成后调用 (第1615-1616行)
```python
# 发送Slack通知
if self.slack:
    await self._send_sell_notification(symbol, signal, order, final_quantity, final_price)

# 🔥 卖出后检查并唤醒延迟信号（资金释放后可能可以处理）
await self._check_delayed_signals()
```

**工作流程**：
1. 卖出订单成功 → 释放资金
2. 检查是否有延迟信号
3. 如果有，唤醒它们（移除 retry_after）
4. 这些信号将在下次循环中立即被处理

## 📊 优化效果对比

### 优化前

| 场景 | 延迟时间 | 处理效率 |
|------|---------|---------|
| 单个信号资金不足 | 2分钟 | 低 |
| 批次中1个不足，9个充足 | 全部延迟2分钟 | 极低 |
| 卖出后释放资金 | 仍需等待2分钟 | 低 |

**问题示例**：
```
批次有10个信号：
- 信号1: $5000 - 成功 ✅
- 信号2: $5000 - 成功 ✅
- 信号3: $8000 - 资金不足 ❌
- 信号4-10: 【全部延迟2分钟】⏰（即使它们只需要$2000）
```

### 优化后

| 场景 | 延迟时间 | 处理效率 |
|------|---------|---------|
| 单个信号资金不足 | 1分钟 | 中 |
| 批次中1个不足，9个充足 | 只延迟1个，其余立即处理 | 高 |
| 卖出后释放资金 | 立即唤醒 | 高 |

**优化示例**：
```
批次有10个信号：
- 信号1: $5000 - 成功 ✅
- 信号2: $5000 - 成功 ✅
- 信号3: $8000 - 资金不足，延迟1分钟 ⏰
- 信号4: $2000 - 继续处理，成功 ✅
- 信号5: $3000 - 继续处理，成功 ✅
- ...
- 信号10: $2000 - 继续处理，成功 ✅

【卖出某持仓后】
→ 立即唤醒信号3 ⚡
→ 信号3在下次循环中立即处理，无需等待1分钟
```

## 🚀 如何使用

### 自动生效

修改已自动生效，无需额外操作。系统会：
1. 使用新的1分钟延迟配置
2. 只延迟资金不足的单个信号
3. 卖出后自动唤醒延迟信号

### 重启服务应用配置

```bash
# 重启订单执行器以应用新配置
./scripts/manage_accounts.sh restart paper_001
./scripts/manage_accounts.sh restart live_001
```

### 手动测试唤醒功能

如果需要手动唤醒延迟信号，可以使用以下Python代码：

```python
import asyncio
from longport_quant.config import get_settings
from longport_quant.messaging.signal_queue import SignalQueue

async def wake_up_signals():
    settings = get_settings()
    queue = SignalQueue(
        redis_url=settings.redis_url,
        queue_key=settings.signal_queue_key
    )

    # 统计延迟信号
    count = await queue.count_delayed_signals(account="paper_001")
    print(f"延迟信号数量: {count}")

    # 唤醒延迟信号
    woken = await queue.wake_up_delayed_signals(account="paper_001")
    print(f"已唤醒: {woken} 个信号")

    await queue.close()

asyncio.run(wake_up_signals())
```

## 📁 涉及文件清单

1. `configs/accounts/paper_001.env` - 配置修改
2. `scripts/order_executor.py` - 批量延迟逻辑修复 + 资金监控触发
3. `src/longport_quant/messaging/signal_queue.py` - 延迟信号统计和唤醒方法
4. `docs/SIGNAL_QUEUE_OPTIMIZATION.md` - 文档（本文件）

## 🔍 监控和日志

### 正常运行日志

```
🚀 开始处理批次: 10个信号
--- [1/10] 处理信号: 883.HK ---
  ✅ 处理完成

--- [3/10] 处理信号: 1234.HK ---
  ⚠️ 资金不足
  💡 策略：仅延迟当前信号，继续处理后续7个信号

--- [4/10] 处理信号: 5678.HK ---
  ✅ 处理完成

⚠️ 批次处理完成: 部分信号资金不足
  已处理: 10个信号
  成功/失败: 9/1个
  待重试: 1个信号（资金不足）
  ✅ 已重新入队: 1个信号
```

### 卖出触发唤醒日志

```
✅ 平仓订单已完成: 123456789
   标的: 883.HK
   原因: 止盈
   数量: 1000股
   平均价: $12.50

💰 卖出后资金释放，检测到3个延迟信号，尝试唤醒...
⏰ 唤醒延迟信号: 1234.HK (账号=paper_001)
⏰ 唤醒延迟信号: 5678.HK (账号=paper_001)
⏰ 唤醒延迟信号: 9012.HK (账号=paper_001)
✅ 已唤醒3个延迟信号（账号=paper_001）
```

## 💡 进一步优化建议

### 短期（可考虑）

1. **动态延迟时间**：根据历史成功率动态调整延迟时间
   - 如果历史上1分钟后成功率高 → 保持1分钟
   - 如果历史上需要更长时间 → 延长至2分钟

2. **按币种分别统计**：HKD和USD资金分别监控
   - 卖出HKD持仓 → 只唤醒HKD信号
   - 卖出USD持仓 → 只唤醒USD信号

### 中期（需要较大改动）

1. **预估资金需求**：延迟信号时记录所需资金量
   - 卖出释放$10000 → 只唤醒需要≤$10000的信号
   - 避免唤醒后仍然资金不足

2. **智能优先级调整**：资金不足多次的信号降低优先级
   - 避免高价信号反复阻塞队列

## ✅ 验收标准

- [x] 配置文件修改完成（FUNDS_RETRY_DELAY=1, FUNDS_RETRY_MAX=5）
- [x] 批量延迟逻辑修复完成（移除级联延迟）
- [x] SignalQueue 新增方法完成（count + wake_up）
- [x] OrderExecutor 资金监控触发完成
- [x] 代码语法检查通过
- [x] 文档更新完成
- [ ] 实际运行测试（待服务重启后验证）

## 📞 技术支持

如有问题，请查看：
1. 系统日志：`logs/order_executor_*.log`
2. Redis队列状态：使用 `redis-cli` 查看 `trading:signals`
3. 相关代码：
   - `scripts/order_executor.py` (订单执行器)
   - `src/longport_quant/messaging/signal_queue.py` (信号队列)

---

**实施日期**：2025-11-03
**实施人员**：Claude Code
**状态**：✅ 已完成编码，待测试验证
