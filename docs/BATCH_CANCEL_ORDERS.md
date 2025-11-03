# 批量取消历史订单功能

## 概述

本功能用于批量取消历史的GTC订单（撤单前有效），帮助清理过期的监控订单。

## 功能特性

- ✅ 批量取消订单：支持一次性取消多个订单
- ✅ 预览模式：先查看将要取消的订单，确认后再执行
- ✅ 灵活的日期筛选：可指定保留天数（默认1天）
- ✅ 标的筛选：可指定特定标的的订单
- ✅ 安全保护：只取消可取消状态的订单（New、PartialFilled、WaitToNew）
- ✅ 详细报告：显示取消成功/失败的详细信息

## 快速开始

### 1. 预览模式（推荐首次使用）

先运行预览模式，查看将要取消的订单：

```bash
python scripts/cancel_old_orders.py --dry-run
```

输出示例：
```
================================================================================
                        📋 批量取消历史订单工具
================================================================================

配置：
  保留天数:     1 天
  截止日期:     2025-11-02 （该日期之前的订单将被取消）
  标的筛选:     全部
  预览模式:     是

正在查询历史订单...

找到 1000 个历史订单：

  Rejected: 850 个
  Filled: 120 个
  Canceled: 25 个
  New: 5 个

可取消的订单 (5 个)：
--------------------------------------------------------------------------------

1398.HK (3 个订单):
  • 116898510071... | SELL |   1000 @ $  100.50 | New             | 2025-10-30 09:30:15
  • 116898479422... | SELL |   2000 @ $  101.00 | New             | 2025-10-30 10:15:22
  • 116897247761... | SELL |   1500 @ $   99.80 | New             | 2025-10-29 14:20:30

NVDA.US (2 个订单):
  • 116896985697... | SELL |    100 @ $  450.00 | PartialFilled   | 2025-10-28 21:30:45
  • 116896732065... | SELL |    200 @ $  445.50 | New             | 2025-10-28 20:45:12

================================================================================

✅ 【预览完成】以上是将要取消的订单。

要实际执行取消操作，请运行:
  python cancel_old_orders.py --keep-days 1

================================================================================
```

### 2. 执行批量取消

确认无误后，执行实际取消操作：

```bash
python scripts/cancel_old_orders.py --keep-days 1
```

系统会要求确认：
```
⚠️  警告：即将取消 5 个订单！

请仔细确认以上订单列表。此操作不可撤销！

确认要继续吗？(yes/no):
```

输入 `yes` 后执行取消。

### 3. 跳过确认（自动化场景）

如果需要自动化执行（例如定时任务），可以跳过确认：

```bash
python scripts/cancel_old_orders.py --keep-days 1 --no-confirm
```

⚠️ **警告**：使用 `--no-confirm` 选项会直接执行取消操作，请谨慎使用！

## 使用场景

### 场景1：清理所有历史订单（只保留今日）

```bash
# 预览
python scripts/cancel_old_orders.py --dry-run

# 执行
python scripts/cancel_old_orders.py --keep-days 1
```

### 场景2：指定账号清理（多账号支持）⭐

```bash
# 清理 paper_001 账号的历史订单
python scripts/cancel_old_orders.py --account paper_001 --keep-days 1

# 清理 live_001 账号的历史订单
python scripts/cancel_old_orders.py --account live_001 --keep-days 1
```

### 场景3：保留最近3天的订单

```bash
python scripts/cancel_old_orders.py --keep-days 3
```

### 场景4：清理特定标的的历史订单

```bash
python scripts/cancel_old_orders.py --symbol 1398.HK --keep-days 1
```

### 场景5：定期自动清理

创建一个cron任务，每天凌晨自动清理：

```bash
# 编辑 crontab
crontab -e

# 添加以下行（每天凌晨2点执行）
0 2 * * * cd /data/web/longport-quant-new && python3 scripts/cancel_old_orders.py --keep-days 1 --no-confirm >> logs/cancel_orders.log 2>&1
```

## 命令行参数

| 参数 | 说明 | 默认值 | 示例 |
|------|------|--------|------|
| `--dry-run` | 预览模式，不实际执行取消 | 否 | `--dry-run` |
| `--keep-days` | 保留天数 | 1 | `--keep-days 7` |
| `--account` | 指定账号ID（多账号支持）⭐ | 默认账号 | `--account paper_001` |
| `--symbol` | 指定标的代码 | 全部 | `--symbol 1398.HK` |
| `--no-confirm` | 跳过确认提示 | 否 | `--no-confirm` |

## 可取消的订单状态

系统会取消以下状态的订单：

- **New**：新订单（已提交到交易所）
- **PartialFilled**：部分成交
- **WaitToNew**：等待提交
- **VarietiesNotReported**：品种未报告（GTC条件单常见状态）⭐
- **NotReported**：未报告

> ⚠️ **重要说明**：`VarietiesNotReported` 状态通常出现在 GTC（撤单前有效）条件单中，这些订单虽然创建于历史日期，但因为一直有效，会持续显示在"今日订单"中。本工具会根据订单的**创建时间**（而非显示位置）来判断是否为历史订单。

以下状态的订单**不会**被取消：

- **Filled**：已完全成交
- **Canceled**：已取消
- **Rejected**：被拒绝
- **Expired**：已过期

## 代码集成

如果你想在代码中使用批量取消功能，可以这样做：

### 方式1：使用 OrderManager（推荐）

```python
from longport_quant.config import get_settings
from longport_quant.execution.client import LongportTradingClient
from longport_quant.persistence.order_manager import OrderManager

async def cancel_old_orders_example():
    settings = get_settings()
    order_manager = OrderManager()

    async with LongportTradingClient(settings) as client:
        # 预览模式
        result = await order_manager.cancel_old_orders(
            trade_client=client,
            keep_days=1,
            dry_run=True
        )

        print(f"查询到 {result['total_found']} 个历史订单")
        print(f"可取消 {result['cancelable']} 个订单")

        # 如果确认要取消
        if result['cancelable'] > 0:
            result = await order_manager.cancel_old_orders(
                trade_client=client,
                keep_days=1,
                dry_run=False
            )
            print(f"成功取消 {result['cancelled']} 个订单")
```

### 方式2：直接使用 TradeClient

```python
from longport_quant.config import get_settings
from longport_quant.execution.client import LongportTradingClient

async def batch_cancel_example():
    settings = get_settings()

    async with LongportTradingClient(settings) as client:
        # 先查询要取消的订单
        history_orders = await client.history_orders(
            start_at=datetime.now() - timedelta(days=7),
            end_at=datetime.now() - timedelta(days=1)
        )

        # 过滤可取消的订单
        order_ids = [
            order.order_id
            for order in history_orders
            if str(order.status).replace("OrderStatus.", "") in ["New", "PartialFilled", "WaitToNew"]
        ]

        # 批量取消
        result = await client.cancel_orders_batch(order_ids)

        print(f"总共: {result['total']}")
        print(f"成功: {result['succeeded']}")
        print(f"失败: {result['failed']}")

        # 查看失败详情
        if result['failed'] > 0:
            for order_id, error in result['errors'].items():
                print(f"订单 {order_id} 取消失败: {error}")
```

## 技术实现

### 新增的代码模块

1. **TradeClient.cancel_orders_batch()** (`src/longport_quant/execution/client.py:321-383`)
   - 批量取消订单的底层API调用
   - 支持错误处理和详细报告

2. **OrderManager.get_orders_by_date_range()** (`src/longport_quant/persistence/order_manager.py:269-315`)
   - 按日期范围查询订单

3. **OrderManager.get_old_orders()** (`src/longport_quant/persistence/order_manager.py:317-349`)
   - 获取历史订单（超过指定天数）

4. **OrderManager.cancel_old_orders()** (`src/longport_quant/persistence/order_manager.py:351-460`)
   - 批量取消历史订单的主方法
   - 支持预览模式和实际执行

5. **清理脚本** (`scripts/cancel_old_orders.py`)
   - 命令行工具，方便手动执行

## 安全性说明

1. **预览模式**：建议首次使用时先运行预览模式，确认订单列表无误
2. **确认提示**：默认会要求用户确认，防止误操作
3. **状态检查**：只取消可取消状态的订单，保护已成交订单
4. **日志记录**：详细记录每个订单的取消结果

## 常见问题

### Q1: 为什么查询不到券商的历史订单？

A: 券商API的历史订单查询通常有时间限制（如最近30天）。如果订单创建时间太早，可能无法通过API查询到。

### Q2: 为什么有些订单取消失败？

A: 可能的原因：
- 订单已经被取消或成交
- 订单状态已经变为不可取消状态
- 网络问题导致API调用失败

### Q3: 如何查看被取消的订单详情？

A: 被取消的订单会在数据库中更新状态为 "Canceled"。你可以通过以下方式查看：

```bash
python scripts/check_broker_orders.py
```

### Q4: 可以撤销已取消的订单吗？

A: 不可以。订单一旦取消，无法恢复。这就是为什么我们提供预览模式的原因。

## 联系与支持

如果遇到问题或有改进建议，请提交Issue或联系开发团队。

## 更新日志

- **2025-11-03**: 初始版本发布
  - 添加批量取消订单功能
  - 添加预览模式
  - 添加命令行工具
