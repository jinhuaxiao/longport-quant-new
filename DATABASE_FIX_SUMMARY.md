# ✅ 数据库修正总结

## 🔍 问题发现

用户发现文档中使用了 `sqlite3` 命令查询订单，但系统实际使用的是 **PostgreSQL** 数据库。

## 🛠️ 已修正内容

### 1. 凯利公式模块 (`src/longport_quant/risk/kelly.py`)

#### 修正前
- 使用 SQLite (`trading.db`)
- 同步方法 (`get_trading_stats()`)
- 查询不存在的表结构

#### 修正后
- ✅ 使用 PostgreSQL（从环境变量 `DATABASE_DSN` 读取）
- ✅ 改为异步方法 (`async def get_trading_stats()`)
- ✅ 从 `position_stops` 表读取交易历史
- ✅ 添加连接池管理
- ✅ 添加 `close()` 方法清理资源

**关键改动**:
```python
# 修正前
def __init__(self, db_path: str = "trading.db", ...):
    self.db_path = db_path
    conn = sqlite3.connect(self.db_path)

# 修正后
def __init__(self, ...):
    db_url = os.getenv('DATABASE_DSN', 'postgresql://...')
    self.pool = await asyncpg.create_pool(db_url)
```

### 2. 信号生成器 (`scripts/signal_generator.py`)

#### 修正
- ✅ 移除 `db_path="trading.db"` 参数
- ✅ 更新初始化代码
- ✅ 添加注释说明使用 PostgreSQL

**改动**:
```python
# 修正前
self.kelly_calculator = KellyCalculator(
    db_path="trading.db",
    ...
)

# 修正后
self.kelly_calculator = KellyCalculator(
    ...  # 自动从环境变量读取 PostgreSQL 配置
)
```

### 3. 文档更新

#### 新增文档
- ✅ `docs/DATABASE_QUERIES.md` - PostgreSQL 查询命令参考
  - 查看今日订单
  - 查看交易历史
  - 统计胜率和盈亏比
  - 查看当前持仓
  - 实用查询脚本

---

## 📊 正确的数据库结构

### PostgreSQL 表结构

系统使用以下 PostgreSQL 表：

1. **`orderrecord`** - 订单记录
   - order_id, symbol, side, price, quantity, status
   - created_at, updated_at

2. **`position_stops`** - 交易历史（凯利公式数据源）⭐
   - symbol, entry_price, exit_price, pnl
   - stop_loss, take_profit, atr
   - status (active, hit_stop_loss, hit_take_profit, closed)
   - entry_time, exit_time

3. **`positions`** - 当前持仓
   - symbol, quantity, cost_price, market_value
   - unrealized_pnl, realized_pnl

---

## 🔍 正确的查询命令

### 查看今日订单（修正后）

```bash
# ❌ 错误（原文档）
sqlite3 trading.db "SELECT * FROM orders WHERE date(created_at) = date('now')"

# ✅ 正确
psql -h 127.0.0.1 -U postgres -d longport_next_new -c \
  "SELECT symbol, side, price, status, created_at FROM orderrecord \
   WHERE DATE(created_at) = CURRENT_DATE ORDER BY created_at DESC;"
```

### 查看交易历史（凯利公式数据）

```bash
# 查看最近 30 天已平仓交易
psql -h 127.0.0.1 -U postgres -d longport_next_new -c \
  "SELECT symbol, entry_price, exit_price, pnl, exit_time \
   FROM position_stops \
   WHERE exit_time >= NOW() - INTERVAL '30 days' \
   AND status IN ('hit_stop_loss', 'hit_take_profit', 'closed') \
   ORDER BY exit_time DESC LIMIT 20;"
```

### 统计胜率和盈亏比

```sql
-- 凯利公式使用的统计数据
WITH trades AS (
    SELECT
        CASE WHEN exit_price > entry_price THEN 1 ELSE 0 END as is_win,
        (exit_price - entry_price) / entry_price as pnl_pct
    FROM position_stops
    WHERE exit_time >= NOW() - INTERVAL '30 days'
        AND status IN ('hit_stop_loss', 'hit_take_profit', 'closed')
        AND entry_price > 0
        AND exit_price > 0
)
SELECT
    COUNT(*) as total_trades,
    SUM(is_win) as wins,
    ROUND((SUM(is_win)::numeric / COUNT(*) * 100), 2) as win_rate_pct,
    ROUND(AVG(CASE WHEN is_win = 1 THEN pnl_pct END) * 100, 2) as avg_win_pct,
    ROUND(AVG(CASE WHEN is_win = 0 THEN ABS(pnl_pct) END) * 100, 2) as avg_loss_pct
FROM trades;
```

---

## ✅ 验证修正

### 1. 语法检查
```bash
python3 -m py_compile src/longport_quant/risk/kelly.py
python3 -m py_compile scripts/signal_generator.py
# ✅ 通过
```

### 2. 数据库连接测试
```bash
psql -h 127.0.0.1 -U postgres -d longport_next_new -c "SELECT NOW();"
# ✅ 连接成功
```

### 3. 表结构检查
```bash
psql -h 127.0.0.1 -U postgres -d longport_next_new -c "\d position_stops"
# ✅ 表存在，包含 entry_price, exit_price, exit_time 字段
```

---

## 🚀 影响和改进

### 影响
- ✅ 凯利公式现在可以从 **真实的交易历史** 读取数据
- ✅ 不再依赖空的 `trading.db` 文件
- ✅ 与系统其他部分使用相同的数据库
- ✅ 数据一致性得到保证

### 改进
- ✅ 异步查询，性能更好
- ✅ 连接池管理，更高效
- ✅ 统一数据源，维护更简单
- ✅ 文档完善，查询命令正确

---

## 📝 使用建议

### 首次使用

由于是新系统，可能还没有足够的交易历史数据：

1. **初期（< 10 笔交易）**:
   - 凯利公式会自动使用回退策略（固定 10% 仓位）
   - 系统会记录每笔交易到 `position_stops` 表

2. **积累期（10-30 笔交易）**:
   - 凯利公式开始生效
   - 建议观察计算结果是否合理

3. **成熟期（> 30 笔交易）**:
   - 统计数据充足，凯利公式准确
   - 可以看到明显的仓位优化效果

### 监控命令

```bash
# 检查交易历史记录数
psql -h 127.0.0.1 -U postgres -d longport_next_new -c \
  "SELECT COUNT(*) as total_trades FROM position_stops \
   WHERE status IN ('hit_stop_loss', 'hit_take_profit', 'closed');"

# 查看最近一笔交易
psql -h 127.0.0.1 -U postgres -d longport_next_new -c \
  "SELECT * FROM position_stops \
   WHERE status IN ('hit_stop_loss', 'hit_take_profit', 'closed') \
   ORDER BY exit_time DESC LIMIT 1;"
```

---

## 🎉 总结

**修正完成！** 系统现在正确使用 PostgreSQL 数据库：

- ✅ 凯利公式从 `position_stops` 表读取真实交易历史
- ✅ 所有模块使用统一的 PostgreSQL 数据库
- ✅ 查询命令文档已更新
- ✅ 异步实现，性能更好

**下一步**: 启动系统，让它积累交易数据，凯利公式会自动从真实历史中学习并优化仓位！🚀

---

**修正时间**: 2025-11-07
**修正人员**: Claude Code
**影响模块**: kelly.py, signal_generator.py, 文档
