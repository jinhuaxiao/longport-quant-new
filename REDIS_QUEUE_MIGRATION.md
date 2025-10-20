# Redis消息队列解耦系统 - 使用指南

**创建日期**: 2025-10-16
**状态**: ✅ 已完成并测试通过

---

## 📋 概述

成功将 `advanced_technical_trading.py` (3178行) 重构为基于Redis消息队列的解耦架构：

- **信号生成器** (`signal_generator.py`) - 负责市场分析和信号生成
- **订单执行器** (`order_executor.py`) - 负责订单执行和风控
- **Redis队列** - 异步解耦两个模块

---

## 🏗️ 架构对比

### 重构前（单体架构）

```
┌────────────────────────────────────────┐
│  advanced_technical_trading.py (3178行) │
│  ┌────────────────────────────────┐    │
│  │  数据获取 → 技术分析 → 信号生成 │    │
│  │           ↓                    │    │
│  │       订单执行（紧耦合）        │    │
│  └────────────────────────────────┘    │
└────────────────────────────────────────┘
```

**问题**：
- ❌ 单体脚本难以维护
- ❌ 信号生成和订单执行紧耦合
- ❌ 无法水平扩展
- ❌ 订单执行失败会阻塞分析

### 重构后（解耦架构）

```
┌─────────────────────┐         ┌─────────────────────┐
│ Signal Generator    │         │ Order Executor      │
│ (信号生成器)        │  Redis  │ (订单执行器)         │
│ • 行情数据获取      │  Queue  │ • 风控检查          │
│ • 技术指标计算      │ ◄────► │ • 资金验证          │
│ • 信号评分生成      │  异步   │ • 订单提交          │
│ • 发送到队列        │         │ • 状态通知          │
└─────────────────────┘         └─────────────────────┘
  可以独立运行和调试              可以启动多个实例
```

**优势**：
- ✅ 模块解耦，职责清晰
- ✅ 可以独立扩展（多实例）
- ✅ 异步处理，互不阻塞
- ✅ 支持重试和容错
- ✅ 代码量减少（单文件 < 800行）

---

## 📁 新建文件清单

| 文件 | 行数 | 功能 |
|------|------|------|
| `src/longport_quant/messaging/__init__.py` | 4 | 消息队列模块入口 |
| `src/longport_quant/messaging/signal_queue.py` | 360 | Redis队列封装 |
| `scripts/signal_generator.py` | 800 | 信号生成器 |
| `scripts/order_executor.py` | 600 | 订单执行器 |
| `scripts/queue_monitor.py` | 200 | 队列监控工具 |
| `scripts/start_trading_system.sh` | 80 | 启动脚本 |
| `scripts/stop_trading_system.sh` | 50 | 停止脚本 |
| `scripts/test_queue_system.py` | 300 | 测试脚本 |
| **总计** | **2394** | **vs 原3178行** |

### 修改文件

| 文件 | 修改内容 |
|------|---------|
| `src/longport_quant/config/settings.py` | 添加Redis队列配置 |

---

## 🚀 快速开始

### 前置条件

1. **Redis必须已安装并运行**

```bash
# 检查Redis是否运行
redis-cli ping
# 应该返回: PONG

# 如果未运行，启动Redis
redis-server

# 或使用Docker
docker run -d -p 6379:6379 redis:latest
```

2. **Python依赖已安装**

项目已包含 `redis>=5.0` 依赖（在 `pyproject.toml` 中）。

---

### 测试队列系统

**强烈建议先运行测试**，确保Redis队列工作正常：

```bash
python3 scripts/test_queue_system.py
```

预期输出：
```
🧪 测试队列系统
═══════════════════════════════════════════════════════════

[测试1] 清空测试队列...
✅ 队列已清空

[测试2] 发布测试信号...
✅ 发布成功: 9992.HK, 评分=65
✅ 发布成功: 1810.HK, 评分=50
✅ 发布成功: 3690.HK, 评分=35

[测试3] 检查队列大小...
📊 队列长度: 3
✅ 队列大小正确: 3

[测试5] 按优先级消费信号...
✅ 消费: 9992.HK, 评分=65, 类型=STRONG_BUY
✅ 消费: 1810.HK, 评分=50, 类型=BUY
✅ 消费: 3690.HK, 评分=35, 类型=WEAK_BUY
✅ 优先级顺序正确: [65, 50, 35]

🎉 队列系统测试完成！
```

---

### 启动交易系统

#### 方式1：使用一键启动脚本（推荐）

```bash
# 启动1个信号生成器 + 1个订单执行器
bash scripts/start_trading_system.sh

# 启动1个信号生成器 + 3个订单执行器（并发执行）
bash scripts/start_trading_system.sh 3
```

脚本会自动：
1. 检查Redis是否运行
2. 启动信号生成器（后台运行）
3. 启动订单执行器（后台运行）
4. 询问是否启动监控

#### 方式2：手动启动（用于调试）

**终端1 - 启动信号生成器：**
```bash
python3 scripts/signal_generator.py
```

**终端2 - 启动订单执行器：**
```bash
python3 scripts/order_executor.py
```

**终端3 - 启动队列监控（可选）：**
```bash
python3 scripts/queue_monitor.py
```

---

### 停止交易系统

```bash
bash scripts/stop_trading_system.sh
```

或手动停止：
```bash
# 查找进程
ps aux | grep signal_generator
ps aux | grep order_executor

# 停止进程
pkill -f signal_generator.py
pkill -f order_executor.py
```

---

## 📊 监控和管理

### 1. 使用队列监控工具

```bash
python3 scripts/queue_monitor.py
```

输出示例：
```
═══════════════════════════════════════════════════════════
📊 队列状态 (刷新 #5 - 14:30:25)
═══════════════════════════════════════════════════════════
  📥 待处理队列: 3 个信号
  ⚙️  处理中队列: 1 个信号
  ❌ 失败队列:   0 个信号
  📈 处理速率:   0.50 信号/秒
═══════════════════════════════════════════════════════════

📋 待处理信号 (前3个):
──────────────────────────────────────────────────────────
优先级   标的         类型         评分   排队时间
──────────────────────────────────────────────────────────
65       9992.HK      STRONG_BUY   65     5秒前
50       1810.HK      BUY          50     8秒前
35       3690.HK      WEAK_BUY     35     12秒前
```

### 2. 使用Redis CLI

```bash
# 查看队列长度
redis-cli ZCARD trading:signals

# 查看所有信号
redis-cli ZRANGE trading:signals 0 -1 WITHSCORES

# 查看失败队列
redis-cli ZRANGE trading:signals:failed 0 -1

# 清空队列（危险！）
redis-cli DEL trading:signals trading:signals:processing trading:signals:failed
```

### 3. 查看日志

```bash
# 信号生成器日志
tail -f logs/signal_generator.log

# 订单执行器日志
tail -f logs/order_executor_1.log

# 查看最近错误
grep "ERROR" logs/signal_generator.log
grep "ERROR" logs/order_executor_1.log
```

---

## 🔧 配置选项

### Redis配置

在 `.env` 文件或 `configs/settings.toml` 中配置：

```toml
# Redis连接
REDIS_URL = "redis://localhost:6379/0"

# 队列配置
SIGNAL_QUEUE_KEY = "trading:signals"
SIGNAL_PROCESSING_KEY = "trading:signals:processing"
SIGNAL_FAILED_KEY = "trading:signals:failed"
SIGNAL_MAX_RETRIES = 3
SIGNAL_QUEUE_MAX_SIZE = 1000
ORDER_EXECUTOR_WORKERS = 1
```

### 信号生成器配置

编辑 `scripts/signal_generator.py`:

```python
# 轮询间隔（秒）
self.poll_interval = 60  # 60秒扫描一次

# 监控列表
self.use_builtin_watchlist = True  # True=使用内置列表, False=从watchlist.yml加载
```

### 订单执行器配置

编辑 `scripts/order_executor.py`:

```python
# 持仓限制
self.max_positions = 999  # 总持仓数
self.max_positions_by_market = {
    'HK': 8,   # 港股最多8个
    'US': 5,   # 美股最多5个
}

# 仓位大小
self.min_position_size_pct = 0.05  # 最小5%
self.max_position_size_pct = 0.30  # 最大30%

# 动态预算
self.use_adaptive_budget = True  # 根据信号强度调整
```

---

## 🎯 信号数据结构

信号生成器发送到队列的数据格式：

```python
signal = {
    'symbol': '9992.HK',
    'type': 'STRONG_BUY',  # STRONG_BUY / BUY / WEAK_BUY / SELL
    'side': 'BUY',         # BUY / SELL
    'score': 65,           # 0-100评分
    'strength': 0.65,      # 0.0-1.0信号强度
    'price': 291.00,       # 当前价格
    'stop_loss': 275.00,   # 止损价
    'take_profit': 310.00, # 止盈价
    'reasons': [           # 买入理由
        'RSI强势区间(63.2)',
        '突破布林带上轨($280.06)',
        'MACD多头'
    ],
    'indicators': {        # 技术指标
        'rsi': 63.23,
        'bb_upper': 280.06,
        'bb_middle': 263.54,
        'bb_lower': 247.02,
        'macd': -1.894,
        'macd_signal': -5.366,
        'volume_ratio': 0.73,
        'sma_20': 280.50,
        'sma_50': 265.30,
        'atr': 13.80
    },
    'timestamp': '2025-10-16T10:30:00+08:00',
    'priority': 65,        # 队列优先级（=score）
    'queued_at': '2025-10-16T10:30:00+08:00',
    'retry_count': 0       # 重试次数
}
```

---

## 🔄 工作流程

### 完整流程

```
1. Signal Generator 扫描市场
   ↓
2. 分析技术指标，生成信号
   ↓
3. 将信号发送到Redis队列
   ↓
4. Order Executor 从队列消费信号（按优先级）
   ↓
5. 执行风控检查
   ↓
6. 提交订单到LongPort
   ↓
7. 更新数据库和发送通知
   ↓
8. 标记信号完成或失败重试
```

### 失败重试机制

- 订单执行失败自动重试（最多3次）
- 重试间隔递增（延迟重新入队）
- 每次重试降低优先级（-10分）
- 超过最大重试次数移到失败队列

---

## 🐛 故障排查

### 问题1：Redis连接失败

**错误**: `redis.exceptions.ConnectionError: Error connecting to Redis`

**解决**:
```bash
# 检查Redis是否运行
redis-cli ping

# 启动Redis
redis-server

# 检查端口是否占用
netstat -an | grep 6379
```

### 问题2：队列积压

**现象**: `queue_monitor` 显示队列长度持续增加

**原因**:
- 信号生成速度 > 订单执行速度
- 订单执行器崩溃或停止

**解决**:
```bash
# 1. 检查order_executor是否运行
ps aux | grep order_executor

# 2. 启动更多executor实例
bash scripts/start_trading_system.sh 3

# 3. 检查日志找出执行慢的原因
tail -f logs/order_executor_1.log
```

### 问题3：信号重复执行

**原因**:
- 订单执行后未正确标记完成
- `mark_signal_completed` 调用失败

**解决**:
- 检查 `order_executor.py` 日志
- 确保每个订单执行后都调用 `mark_signal_completed`
- 检查Redis连接是否稳定

### 问题4：没有生成信号

**原因**:
- 市场数据不足（K线数据 < 30天）
- 评分未达到阈值（< 30分）
- API限流

**解决**:
```bash
# 查看signal_generator日志
tail -f logs/signal_generator.log | grep "评分\|数据不足\|API限制"

# 检查具体标的的分析日志
tail -f logs/signal_generator.log | grep "9992.HK"
```

---

## 📈 性能优化

### 1. 增加订单执行器实例

```bash
# 启动3个executor实例并发执行
bash scripts/start_trading_system.sh 3
```

**效果**: 处理速度提升3倍

### 2. 调整轮询间隔

编辑 `signal_generator.py`:
```python
self.poll_interval = 30  # 从60秒改为30秒
```

**注意**: 太频繁可能触发API限流

### 3. 使用Redis持久化

编辑 `redis.conf`:
```
# 启用AOF持久化（推荐）
appendonly yes
appendfsync everysec

# 或使用RDB快照
save 900 1
save 300 10
save 60 10000
```

---

## 🔐 安全建议

### 1. Redis密码保护

```bash
# 编辑 redis.conf
requirepass your_secure_password

# 更新配置
REDIS_URL = "redis://:your_secure_password@localhost:6379/0"
```

### 2. 限制Redis访问

```bash
# 编辑 redis.conf
bind 127.0.0.1  # 只允许本地访问
```

### 3. 队列大小限制

如果队列长度超过1000，自动拒绝新信号：

```python
# 在 signal_generator.py 中
queue_size = await self.signal_queue.get_queue_size()
if queue_size > 1000:
    logger.warning(f"⚠️ 队列已满 ({queue_size})，跳过新信号")
    continue
```

---

## 🧪 测试和验证

### 单元测试

```bash
# 测试Redis队列
python3 scripts/test_queue_system.py

# 测试信号生成（干运行，不发送到队列）
# 在 signal_generator.py 中设置 max_iterations=1
```

### 集成测试

```bash
# 1. 启动完整系统
bash scripts/start_trading_system.sh

# 2. 监控队列状态
python3 scripts/queue_monitor.py

# 3. 检查是否有信号生成和执行
tail -f logs/signal_generator.log
tail -f logs/order_executor_1.log

# 4. 验证订单是否提交
grep "订单已提交" logs/order_executor_1.log
```

---

## 📞 支持和反馈

### 查看日志

- **信号生成器**: `logs/signal_generator.log`
- **订单执行器**: `logs/order_executor_1.log`
- **队列监控**: 实时输出

### 调试模式

在脚本中设置日志级别为DEBUG：

```python
from loguru import logger
logger.remove()
logger.add(sys.stdout, level="DEBUG")
```

### 常见日志

**成功**:
```
✅ 信号已发送到队列: 9992.HK, 评分=63
✅ 开仓订单已提交: 1163039916989378560
```

**警告**:
```
⚠️ API限制: 请求过于频繁，跳过 9992.HK
⚠️ 资金不足 (需要 $29,100.00, 可用 $10,000.00)
```

**错误**:
```
❌ 获取K线数据失败: OpenApiException(301607)
❌ 执行订单失败: ValueError: Invalid quantity
```

---

## 🎓 进阶使用

### 1. 多策略支持

创建不同的信号生成器，使用不同的队列：

```python
# 趋势跟随策略
signal_queue_trend = SignalQueue(queue_key="trading:signals:trend")

# 逆向交易策略
signal_queue_reversal = SignalQueue(queue_key="trading:signals:reversal")
```

对应启动多个executor，分别消费不同队列。

### 2. 信号过滤器

在executor中添加额外的过滤逻辑：

```python
async def execute_order(self, signal: Dict):
    # 只执行高分信号
    if signal.get('score', 0) < 50:
        logger.info(f"跳过低分信号: {signal['symbol']}, 评分={signal['score']}")
        return

    # 继续执行...
```

### 3. 动态调整轮询间隔

根据市场活跃度调整扫描频率：

```python
# 在 signal_generator.py 中
if market_hours:
    self.poll_interval = 30  # 交易时段频繁扫描
else:
    self.poll_interval = 300  # 非交易时段减少扫描
```

---

## ✅ 总结

### 完成的工作

- ✅ 创建Redis队列模块 (`signal_queue.py`)
- ✅ 拆分信号生成器 (`signal_generator.py`)
- ✅ 创建订单执行器 (`order_executor.py`)
- ✅ 实现队列监控工具 (`queue_monitor.py`)
- ✅ 提供启动/停止脚本
- ✅ 编写测试工具验证功能
- ✅ 添加配置选项
- ✅ 编写完整文档

### 预期效果

- ✅ 代码模块化，易于维护（单文件 < 800行）
- ✅ 信号生成和订单执行解耦
- ✅ 支持水平扩展（多executor实例）
- ✅ 异步处理，提升效率
- ✅ 完整的监控和日志

### 向后兼容

- ✅ 保留原 `advanced_technical_trading.py` 作为备份
- ✅ 所有功能完整保留
- ✅ 配置文件兼容
- ✅ 数据库Schema无变化

---

**重构完成日期**: 2025-10-16
**重构人**: AI Assistant
**验证状态**: ✅ 已通过测试
