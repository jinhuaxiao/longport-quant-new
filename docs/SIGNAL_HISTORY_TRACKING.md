# 信号历史记录和回溯系统

完整的信号日志记录、查询和回溯分析系统。

---

## ✅ 系统状态

**集成状态**: ✅ 已完成生产环境集成

信号历史记录系统已经完整集成到交易系统中：

- ✅ `signal_generator.py` - 自动记录所有生成的信号（买入/卖出）
- ✅ `order_executor.py` - 自动更新信号执行状态
- ✅ 数据库模型和API - 完整的后端支持
- ✅ Web UI界面 - `/signals/history` 页面可视化查询

**使用方式**: 无需任何配置，系统启动后自动记录所有信号。

---

## 📋 功能概述

### 已实现功能

1. **信号持久化存储** ✅
   - 所有生成的信号都保存到数据库
   - 包含完整的技术指标数据
   - 记录执行状态和结果

2. **信号历史查询** ✅
   - 按时间、股票、操作类型过滤
   - 分页和排序
   - 多维度搜索

3. **性能统计分析** ✅
   - 胜率、盈亏统计
   - 信号质量评估
   - 策略表现对比

4. **可视化界面** ✅
   - 信号历史表格
   - 性能指标面板
   - 最佳表现股票

---

## 🗄️ 数据库模型

### SignalHistory 表结构

```sql
CREATE TABLE signal_history (
    -- 主键
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 基本信息
    timestamp DATETIME NOT NULL,           -- 信号生成时间
    symbol VARCHAR(20) NOT NULL,           -- 股票代码
    action VARCHAR(10) NOT NULL,           -- BUY/SELL

    -- 价格信息
    price FLOAT NOT NULL,                  -- 当前价格
    target_price FLOAT,                    -- 目标价格

    -- 信号评分
    signal_score FLOAT NOT NULL,           -- 信号强度评分 0-100
    confidence FLOAT,                      -- 置信度 0-1

    -- 技术指标（JSON）
    indicators JSON,                       -- RSI, MACD, 布林带等

    -- 策略信息
    strategy_name VARCHAR(50),             -- 策略名称
    strategy_params JSON,                  -- 策略参数

    -- 执行状态
    is_executed BOOLEAN DEFAULT FALSE,     -- 是否已执行
    executed_at DATETIME,                  -- 执行时间
    execution_price FLOAT,                 -- 实际成交价格
    execution_quantity INTEGER,            -- 实际成交数量
    order_id VARCHAR(50),                  -- 订单ID
    execution_status VARCHAR(20),          -- success/failed/pending
    execution_error TEXT,                  -- 错误信息

    -- 收益追踪
    entry_price FLOAT,                     -- 入场价格
    exit_price FLOAT,                      -- 出场价格
    pnl FLOAT,                             -- 盈亏
    pnl_percent FLOAT,                     -- 盈亏百分比

    -- 市场环境
    market_trend VARCHAR(20),              -- bullish/bearish/neutral
    volatility FLOAT,                      -- 波动率

    -- 备注
    notes TEXT,

    -- 元数据
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX idx_symbol_timestamp ON signal_history(symbol, timestamp);
CREATE INDEX idx_action_executed ON signal_history(action, is_executed);
CREATE INDEX idx_score_timestamp ON signal_history(signal_score, timestamp);
```

---

## 🔧 如何集成到 signal_generator.py

### 1. 初始化信号记录器

在 `signal_generator.py` 中添加：

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from longport_quant.models.signal_history import SignalHistory, SignalRecorder
from longport_quant.config import get_settings

class SignalGenerator:
    def __init__(self, ...):
        # ... 现有代码 ...

        # 初始化信号记录器
        settings = get_settings()
        engine = create_engine(settings.database_url)
        SessionLocal = sessionmaker(bind=engine)
        self.db_session = SessionLocal()
        self.signal_recorder = SignalRecorder(self.db_session)
```

### 2. 记录生成的信号

在生成信号时记录：

```python
async def analyze_and_send_signals(self):
    """分析市场并发送信号"""

    for symbol in self.watchlist:
        # ... 现有分析代码 ...

        # 计算技术指标
        indicators = {
            'rsi': rsi_value,
            'macd': macd_value,
            'bollinger_position': bollinger_position,
            'volume_ratio': volume_ratio,
            'ma_fast': ma_fast,
            'ma_slow': ma_slow,
        }

        # 生成信号
        if should_buy:
            signal_score = self.calculate_buy_score(indicators)

            # 📝 记录信号到数据库
            signal_record = self.signal_recorder.record_signal(
                symbol=symbol,
                action='BUY',
                price=current_price,
                signal_score=signal_score,
                indicators=indicators,
                strategy_name='MA_Crossover',
                confidence=0.85,
                market_trend='bullish',
                volatility=calculate_volatility(prices),
                notes=f'MA Cross: {ma_fast:.2f} > {ma_slow:.2f}'
            )

            # 发送到队列（现有逻辑）
            await self.signal_queue.send_signal({
                'signal_id': signal_record.id,  # 添加信号ID
                'symbol': symbol,
                'action': 'BUY',
                'price': current_price,
                'score': signal_score,
                # ... 其他字段 ...
            })

            logger.info(f"✅ 信号已生成并记录: {symbol} BUY score={signal_score} id={signal_record.id}")

        elif should_sell:
            # 类似的卖出信号记录
            signal_score = self.calculate_sell_score(indicators)

            signal_record = self.signal_recorder.record_signal(
                symbol=symbol,
                action='SELL',
                price=current_price,
                signal_score=signal_score,
                indicators=indicators,
                strategy_name='MA_Crossover',
                # ...
            )

            await self.signal_queue.send_signal({
                'signal_id': signal_record.id,
                'symbol': symbol,
                'action': 'SELL',
                # ...
            })
```

### 3. 在 order_executor.py 中更新执行状态

订单执行后更新信号记录：

```python
from longport_quant.models.signal_history import SignalRecorder

class OrderExecutor:
    def __init__(self):
        # ... 现有代码 ...

        # 初始化信号记录器
        settings = get_settings()
        engine = create_engine(settings.database_url)
        SessionLocal = sessionmaker(bind=engine)
        self.db_session = SessionLocal()
        self.signal_recorder = SignalRecorder(self.db_session)

    async def execute_signal(self, signal_data):
        """执行信号"""

        signal_id = signal_data.get('signal_id')  # 从信号中获取ID

        try:
            # ... 现有执行逻辑 ...

            order = await self.submit_order(symbol, action, quantity, price)

            # 📝 更新信号执行状态
            if signal_id:
                self.signal_recorder.update_execution(
                    signal_id=signal_id,
                    executed_at=datetime.now(),
                    execution_price=order.executed_price,
                    execution_quantity=order.executed_quantity,
                    order_id=order.order_id,
                    execution_status='success'
                )

            logger.info(f"✅ 订单执行成功，信号已更新: signal_id={signal_id}")

        except Exception as e:
            # 记录执行失败
            if signal_id:
                self.signal_recorder.update_execution(
                    signal_id=signal_id,
                    executed_at=datetime.now(),
                    execution_price=None,
                    execution_quantity=0,
                    order_id=None,
                    execution_status='failed',
                    execution_error=str(e)
                )

            logger.error(f"❌ 订单执行失败，信号已更新: signal_id={signal_id} error={e}")
```

---

## 🌐 API 端点

### 查询信号历史

```bash
# 获取最近50条信号
curl "http://localhost:8000/api/signals/recent?limit=50"

# 过滤特定股票
curl "http://localhost:8000/api/signals/recent?symbol=AAPL&limit=100"

# 过滤未执行的买入信号
curl "http://localhost:8000/api/signals/recent?action=BUY&is_executed=false"

# 过滤高分信号
curl "http://localhost:8000/api/signals/recent?min_score=70"
```

### 获取统计数据

```bash
# 最近30天统计
curl "http://localhost:8000/api/signals/stats?days=30"

# 返回示例：
{
  "period_days": 30,
  "stats": {
    "total_signals": 156,
    "buy_signals": 89,
    "sell_signals": 67,
    "executed_signals": 142,
    "execution_rate": 91.03,
    "average_score": 68.5
  },
  "performance": {
    "total_executed": 142,
    "profitable_count": 95,
    "loss_count": 47,
    "win_rate": 66.9,
    "total_pnl": 1234.56,
    "average_pnl": 8.69
  }
}
```

### 获取特定股票历史

```bash
# AAPL最近30天信号
curl "http://localhost:8000/api/signals/by-symbol/AAPL?days=30"

# 返回示例：
{
  "symbol": "AAPL",
  "period_days": 30,
  "total_signals": 12,
  "buy_signals": 7,
  "sell_signals": 5,
  "executed_signals": 11,
  "execution_rate": 91.67,
  "average_score": 72.3,
  "signals": [...]
}
```

### 获取最佳表现股票

```bash
# 按胜率排序
curl "http://localhost:8000/api/signals/top-performers?days=30&limit=10&sort_by=win_rate"

# 按总盈亏排序
curl "http://localhost:8000/api/signals/top-performers?sort_by=total_pnl"
```

---

## 📊 Web 界面使用

### 访问信号历史页面

```
http://localhost:3000/signals/history
```

### 页面功能

#### 1. 统计概览

顶部显示6个关键指标：
- Total Signals - 总信号数
- Buy Signals - 买入信号数
- Sell Signals - 卖出信号数
- Executed - 已执行数量
- Execution Rate - 执行率
- Avg Score - 平均评分

#### 2. 性能分析

显示：
- Win Rate - 胜率（盈利信号/总执行信号）
- Total P&L - 总盈亏
- Avg P&L per Trade - 平均每笔盈亏
- Trades Breakdown - 盈利/亏损分布

#### 3. 信号历史表格

支持过滤：
- 股票代码 (Symbol)
- 操作类型 (BUY/SELL)
- 最低评分 (Min Score)
- 执行状态 (Executed/Not Executed)

显示列：
- Time - 生成时间
- Symbol - 股票代码
- Action - 操作类型（绿色BUY/红色SELL）
- Price - 当时价格
- Score - 信号评分（70+绿色，50-70黄色）
- Status - 执行状态（Pending/Executed/Failed）
- P&L - 盈亏金额和百分比
- Strategy - 策略名称

#### 4. Top Performers

显示最佳表现股票：
- 可按胜率、总盈亏、信号数量排序
- 显示每个股票的详细统计

---

## 🔍 回溯分析用例

### 用例1：分析信号质量

**问题**：高分信号是否真的表现更好？

**分析方法**：
```python
# 使用API获取数据
import requests

# 获取70分以上的信号
high_score = requests.get(
    'http://localhost:8000/api/signals/recent?min_score=70&is_executed=true&limit=1000'
).json()

# 获取50-70分的信号
mid_score = requests.get(
    'http://localhost:8000/api/signals/recent?min_score=50&is_executed=true&limit=1000'
).json()

# 计算各自的胜率
high_win_rate = len([s for s in high_score['signals'] if s['pnl'] > 0]) / len(high_score['signals'])
mid_win_rate = len([s for s in mid_score['signals'] if s['pnl'] > 0]) / len(mid_score['signals'])

print(f"High score (70+) win rate: {high_win_rate:.2%}")
print(f"Mid score (50-70) win rate: {mid_win_rate:.2%}")
```

### 用例2：找出最可靠的股票

**问题**：哪些股票的信号最准确？

**操作**：
1. 访问 `/signals/history`
2. 切换到 "Top Performers" 标签
3. 选择 "Win Rate" 排序
4. 查看胜率最高的股票

**结果**：
```
Rank  Symbol   Signals  Win Rate  Total P&L
#1    AAPL     25       85.2%     +$1,234.56
#2    MSFT     18       78.9%     +$987.65
#3    TSLA     32       72.3%     +$2,345.12
```

### 用例3：策略回测

**问题**：某个策略在过去30天的表现如何？

```python
# 查询特定策略的信号
signals = requests.get(
    'http://localhost:8000/api/signals/recent?limit=1000'
).json()

# 过滤MA_Crossover策略
ma_signals = [s for s in signals['signals'] if s['strategy_name'] == 'MA_Crossover']

# 统计
total = len(ma_signals)
executed = len([s for s in ma_signals if s['is_executed']])
profitable = len([s for s in ma_signals if s.get('pnl', 0) > 0])
total_pnl = sum(s.get('pnl', 0) for s in ma_signals)

print(f"Strategy: MA_Crossover")
print(f"Total signals: {total}")
print(f"Executed: {executed} ({executed/total*100:.1f}%)")
print(f"Win rate: {profitable/executed*100:.1f}%")
print(f"Total P&L: ${total_pnl:.2f}")
```

### 用例4：识别失败模式

**问题**：为什么有些信号没有执行？

```python
# 查询未执行的信号
unexecuted = requests.get(
    'http://localhost:8000/api/signals/recent?is_executed=false&limit=100'
).json()

# 分析原因（从execution_error字段）
from collections import Counter

errors = [s.get('execution_error') for s in unexecuted['signals'] if s.get('execution_error')]
error_counts = Counter(errors)

print("Top failure reasons:")
for error, count in error_counts.most_common(5):
    print(f"  {count}x: {error}")
```

---

## 📈 性能优化

### 数据库索引

已创建的索引：
```sql
CREATE INDEX idx_symbol_timestamp ON signal_history(symbol, timestamp);
CREATE INDEX idx_action_executed ON signal_history(action, is_executed);
CREATE INDEX idx_score_timestamp ON signal_history(signal_score, timestamp);
```

### 查询优化建议

```python
# ✅ 好的查询 - 使用索引
db.query(SignalHistory).filter(
    SignalHistory.symbol == 'AAPL',
    SignalHistory.timestamp >= start_date
).all()

# ❌ 慢的查询 - 未使用索引
db.query(SignalHistory).filter(
    SignalHistory.notes.like('%pattern%')
).all()
```

### 数据清理

定期清理旧数据：

```bash
# 演练模式（不实际删除）
curl -X DELETE "http://localhost:8000/api/signals/cleanup?days=90&dry_run=true"

# 实际删除90天前的数据
curl -X DELETE "http://localhost:8000/api/signals/cleanup?days=90&dry_run=false"
```

---

## 🎯 最佳实践

### 1. 信号记录

✅ **应该做**：
- 记录所有生成的信号（包括未执行的）
- 包含完整的技术指标数据
- 记录信号生成时的市场环境
- 添加有意义的备注

❌ **不要做**：
- 只记录执行的信号
- 省略技术指标数据
- 忘记更新执行状态

### 2. 数据分析

✅ **定期分析**：
- 每周查看信号质量趋势
- 对比不同策略的表现
- 识别表现最好/最差的股票
- 调整信号评分阈值

### 3. 数据维护

✅ **定期维护**：
- 每月清理90天前的数据
- 备份重要的历史记录
- 监控数据库大小
- 检查索引性能

---

## 🚀 未来增强

### 短期（1-2周）

- [ ] 实时图表可视化（信号分布、时间线）
- [ ] 导出CSV/Excel功能
- [ ] 信号详情弹窗（查看完整技术指标）

### 中期（1个月）

- [ ] 自动化回测报告生成
- [ ] 信号质量自动评级
- [ ] 策略对比分析工具
- [ ] 预警系统（低胜率、高失败率）

### 长期（3个月）

- [ ] 机器学习信号评分优化
- [ ] 多策略组合分析
- [ ] 实时性能监控仪表板
- [ ] A/B测试框架

---

## 📞 故障排查

### 问题1：信号没有保存到数据库

**检查**：
```python
# 测试数据库连接
from sqlalchemy import create_engine
from longport_quant.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url)

# 测试连接
with engine.connect() as conn:
    result = conn.execute("SELECT COUNT(*) FROM signal_history")
    print(f"Total signals in DB: {result.scalar()}")
```

**解决**：
- 检查 `database_url` 配置
- 确保数据库表已创建
- 查看应用日志中的错误信息

### 问题2：Web界面没有数据

**检查**：
```bash
# 直接测试API
curl "http://localhost:8000/api/signals/recent?limit=10"

# 检查CORS配置
curl -H "Origin: http://localhost:3000" \
     -I "http://localhost:8000/api/signals/recent"
```

**解决**：
- 确保后端API运行
- 检查CORS配置
- 查看浏览器控制台错误

---

**现在你可以完整追踪和回溯所有交易信号了！** 📊
