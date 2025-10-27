# Hybrid Stop Loss System (混合止损系统)

## 概述

本系统实现了**混合止损策略**，结合了客户端智能监控和交易所原生条件单，提供最佳的止损止盈保护。

### 设计理念

- **主策略**: 客户端实时监控（支持智能止盈、技术指标判断）
- **备份策略**: LongPort 原生 LIT 条件单（灾难恢复，服务器端执行）
- **Best of Both Worlds**: 灵活性 + 可靠性

---

## 架构设计

### 1. 数据库扩展

**表**: `position_stops`

新增字段:
- `backup_stop_loss_order_id` VARCHAR(50) - 备份止损条件单ID
- `backup_take_profit_order_id` VARCHAR(50) - 备份止盈条件单ID

**迁移脚本**: `scripts/add_backup_order_ids_to_position_stops.py`

### 2. 组件修改

#### A. `StopLossManager` (stop_manager.py)

**新增功能**:
- `save_stop()` 现在接受备份订单ID参数
- `load_active_stops()` 返回包含备份订单ID的完整信息
- `get_stop_for_symbol()` 返回包含备份订单ID

**示例**:
```python
await stop_manager.save_stop(
    symbol="1398.HK",
    entry_price=6.08,
    stop_loss=5.78,
    take_profit=6.68,
    quantity=1000,
    backup_stop_loss_order_id="abc123",
    backup_take_profit_order_id="def456"
)
```

#### B. `LongportTradingClient` (client.py)

**新增方法**: `submit_conditional_order()`

**功能**: 提交 LIT (Limit If Touched) 条件单

**参数**:
- `symbol`: 标的代码
- `side`: "BUY" 或 "SELL"
- `quantity`: 数量
- `trigger_price`: 触发价格（市价到达此价时触发）
- `limit_price`: 限价（触发后以此价格下单）
- `remark`: 备注

**特性**:
- 订单类型: `OrderType.LIT`
- 有效期: `GoodTilCanceled` (直到取消)
- 自动四舍五入到有效tick size

**示例**:
```python
# 止损单：价格跌到 100 时，以 99.5 卖出
result = await client.submit_conditional_order(
    symbol="1398.HK",
    side="SELL",
    quantity=1000,
    trigger_price=100.0,
    limit_price=99.5,
    remark="Backup Stop Loss @ $100.00"
)
order_id = result['order_id']
```

#### C. `OrderExecutor` (order_executor.py)

**修改点1**: BUY 订单成交后（lines 497-551）

**流程**:
1. 买入订单执行完成
2. 提交备份条件单:
   - **止损单**: `trigger_price = stop_loss`, `limit_price = stop_loss * 0.995`
   - **止盈单**: `trigger_price = take_profit`, `limit_price = take_profit`
3. 保存订单ID到数据库
4. 记录日志

**日志示例**:
```
✅ 止损备份条件单已提交: 1234567890
✅ 止盈备份条件单已提交: 0987654321
📋 备份条件单策略: 客户端监控（主） + 交易所条件单（备份）
✅ 已保存 1398.HK 的止损止盈设置到数据库 (备份单: SL=1234567890, TP=0987654321)
```

**修改点2**: SELL 订单执行前（lines 569-596）

**流程**:
1. 客户端监控触发 SELL 信号
2. 查询数据库获取备份条件单ID
3. 尝试取消备份条件单（两个都取消）
4. 继续执行客户端卖出订单

**日志示例**:
```
✅ 已取消备份条件单: 止损单(1234567890), 止盈单(0987654321)
📋 客户端监控触发在先，交易所备份单已作废
```

**错误处理**:
- 如果备份单已触发或不存在 → 记录 debug 日志，继续执行
- 不影响主流程的正常执行

---

## 工作流程

### 场景 1: 正常买入 + 设置备份

```
1. 信号生成器: BUY 信号 → Redis 队列
2. 订单执行器: 执行 BUY 订单
3. 订单执行器: 提交备份 LIT 条件单（止损 + 止盈）
4. 订单执行器: 保存到数据库（包含备份订单ID）
5. 信号生成器: WebSocket 实时监控开始
6. 交易所: 备份条件单持续有效（GTC）
```

**状态**:
- ✅ 客户端监控: 活跃
- ✅ 备份条件单: 挂单中
- ✅ 数据库记录: 已保存

---

### 场景 2: 客户端监控先触发（正常流程）

```
1. 信号生成器: 检测到价格触及止损/止盈
2. 信号生成器: 生成 SELL 信号 → Redis 队列
3. 订单执行器: 接收 SELL 信号
4. 订单执行器: 取消备份条件单（两个都取消）
5. 订单执行器: 执行智能卖出策略
6. 订单执行器: 更新数据库状态
```

**结果**:
- ✅ 使用客户端智能策略（支持技术指标判断）
- ✅ 备份条件单已作废
- ✅ 没有重复交易

---

### 场景 3: 备份条件单先触发（灾难恢复）

```
1. 客户端进程崩溃/网络中断
2. 交易所: 价格到达触发价
3. 交易所: 备份条件单自动触发并成交
4. 持仓已平仓
```

**后续处理**:
- 客户端恢复后，会检测到持仓已不存在
- 信号生成器的去重逻辑会阻止重复卖出
- 订单历史中可以看到备份单成交记录

**优势**:
- 即使客户端完全失效，止损仍然有效
- 服务器端执行，无延迟风险

---

## 技术细节

### LIT 条件单参数

**止损单设置**:
```python
trigger_price = stop_loss                # 例如: $5.78
limit_price = stop_loss * 0.995          # 例如: $5.75（略低以确保成交）
```

**原因**: 止损时市场可能快速下跌，略低的限价确保能够成交

**止盈单设置**:
```python
trigger_price = take_profit              # 例如: $6.68
limit_price = take_profit                # 例如: $6.68（使用触发价本身）
```

**原因**: 止盈时不需要急于成交，使用触发价可以获得更好价格

### 订单有效期

**TimeInForceType.GoodTilCanceled (GTC)**:
- 订单一直有效，直到:
  1. 客户端主动取消
  2. 条件触发并成交
  3. 手动取消

### 错误处理策略

**原则**: 备份条件单失败不影响主流程

**实现**:
- 所有备份单操作都包裹在 `try-except` 中
- 失败时记录警告日志
- 继续执行客户端监控（主策略仍然有效）

---

## 优势对比

| 特性 | 客户端监控（主） | 备份条件单 | 混合策略 |
|-----|-----------------|-----------|---------|
| **智能止盈** | ✅ 支持 | ❌ 不支持 | ✅ 支持 |
| **技术指标** | ✅ 支持 | ❌ 不支持 | ✅ 支持 |
| **灾难恢复** | ❌ 需进程运行 | ✅ 服务器端 | ✅ 双重保护 |
| **延迟** | <1秒 (WebSocket) | 0秒 (交易所) | 最优 |
| **可靠性** | 中 | 高 | 非常高 |
| **灵活性** | 高 | 低 | 高 |
| **复杂度** | 高 | 低 | 中 |

---

## 监控和调试

### 日志关键词

**成功提交备份单**:
```
✅ 止损备份条件单已提交: [order_id]
✅ 止盈备份条件单已提交: [order_id]
📋 备份条件单策略: 客户端监控（主） + 交易所条件单（备份）
```

**取消备份单**:
```
✅ 已取消备份条件单: 止损单([id]), 止盈单([id])
📋 客户端监控触发在先，交易所备份单已作废
```

**备份单失败** (不影响主流程):
```
⚠️ 提交备份条件单失败（不影响主流程）: [error]
⚠️ 查询/取消备份条件单失败（不影响主流程）: [error]
```

### 数据库查询

**查看活跃备份单**:
```sql
SELECT
    symbol,
    stop_loss,
    take_profit,
    backup_stop_loss_order_id,
    backup_take_profit_order_id,
    created_at
FROM position_stops
WHERE status = 'active'
AND (backup_stop_loss_order_id IS NOT NULL
     OR backup_take_profit_order_id IS NOT NULL);
```

**统计备份单使用情况**:
```sql
SELECT
    COUNT(*) as total_stops,
    COUNT(backup_stop_loss_order_id) as with_backup_stop,
    COUNT(backup_take_profit_order_id) as with_backup_profit
FROM position_stops
WHERE status = 'active';
```

---

## 配置建议

### 测试环境

**建议**: 先在模拟账户测试
- 验证备份单能正常提交
- 验证取消逻辑工作正常
- 观察日志确认流程正确

### 生产环境

**监控重点**:
1. 备份单提交成功率
2. 备份单取消成功率
3. 是否出现重复交易（不应该发生）

**告警阈值**:
- 备份单提交失败率 > 10% → 需要检查 API 配额或权限
- 连续多次取消失败 → 可能存在 API 问题

---

## 未来优化方向

### 1. 备份单触发检测
**当前**: 依赖信号去重逻辑防止重复
**优化**: 主动检测备份单成交，更新数据库状态

### 2. 动态调整备份单
**当前**: 备份单一次性设置，不会调整
**优化**: 当客户端调整止损位时，同步更新备份单

### 3. 备份单类型扩展
**当前**: 只支持 LIT 固定触发价
**优化**: 支持 TSLPPCT 追踪止损（跟随价格移动）

### 4. 成本优化
**当前**: 每次买入都提交两个备份单
**优化**: 可配置是否启用备份单（按账户或按标的）

---

## 文件清单

### 新增文件
- `scripts/add_backup_order_ids_to_position_stops.py` - 数据库迁移脚本
- `docs/implementation/HYBRID_STOP_LOSS_SYSTEM.md` - 本文档

### 修改文件
- `src/longport_quant/persistence/stop_manager.py` - 支持备份订单ID
- `src/longport_quant/execution/client.py` - 新增 `submit_conditional_order()` 方法
- `scripts/order_executor.py` - 提交和取消备份条件单逻辑

---

## 总结

混合止损系统通过结合客户端智能监控和交易所原生条件单，实现了：

✅ **高灵活性**: 支持基于技术指标的智能止盈
✅ **高可靠性**: 备份条件单提供灾难恢复能力
✅ **零重复**: 完善的去重机制确保不会重复交易
✅ **易维护**: 失败不影响主流程，日志清晰

这是**量化交易系统止损止盈的最佳实践**。
