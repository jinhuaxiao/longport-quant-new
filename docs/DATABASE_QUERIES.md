# 数据库查询命令参考

系统使用 **PostgreSQL** 数据库存储所有交易数据。

## 📊 数据库连接信息

```bash
# 从 .env 文件中获取
DATABASE_DSN=postgresql+asyncpg://postgres:jinhua@127.0.0.1:5432/longport_next_new

# 使用 psql 连接
psql -h 127.0.0.1 -U postgres -d longport_next_new
# 密码: jinhua
```

---

## 🔍 常用查询

### 1. 查看今日订单

```sql
-- 查看今日所有订单
SELECT
    symbol,
    side,
    ROUND(price::numeric, 2) as price,
    ROUND(quantity::numeric, 2) as quantity,
    status,
    to_char(created_at, 'HH24:MI:SS') as time
FROM orderrecord
WHERE DATE(created_at) = CURRENT_DATE
ORDER BY created_at DESC;
```

```bash
# 命令行方式
psql -h 127.0.0.1 -U postgres -d longport_next_new -c "SELECT symbol, side, ROUND(price::numeric, 2) as price, status, to_char(created_at, 'HH24:MI:SS') as time FROM orderrecord WHERE DATE(created_at) = CURRENT_DATE ORDER BY created_at DESC;"
```

### 2. 查看交易历史（凯利公式数据源）

```sql
-- 查看最近 30 天的已平仓交易
SELECT
    symbol,
    ROUND(entry_price::numeric, 2) as entry,
    ROUND(exit_price::numeric, 2) as exit,
    ROUND(pnl::numeric, 2) as pnl,
    ROUND(((exit_price - entry_price) / entry_price * 100)::numeric, 2) as pnl_pct,
    status,
    to_char(exit_time, 'YYYY-MM-DD HH24:MI') as exit_time
FROM position_stops
WHERE exit_time >= NOW() - INTERVAL '30 days'
    AND status IN ('hit_stop_loss', 'hit_take_profit', 'closed')
ORDER BY exit_time DESC
LIMIT 20;
```

### 3. 统计胜率和盈亏比

```sql
-- 计算最近 30 天的交易统计（凯利公式使用）
WITH trades AS (
    SELECT
        symbol,
        entry_price,
        exit_price,
        CASE
            WHEN exit_price > entry_price THEN 1
            ELSE 0
        END as is_win,
        (exit_price - entry_price) / entry_price as pnl_pct
    FROM position_stops
    WHERE exit_time >= NOW() - INTERVAL '30 days'
        AND status IN ('hit_stop_loss', 'hit_take_profit', 'closed')
        AND entry_price > 0
        AND exit_price > 0
)
SELECT
    COUNT(*) as total_trades,
    SUM(is_win) as winning_trades,
    COUNT(*) - SUM(is_win) as losing_trades,
    ROUND((SUM(is_win)::numeric / COUNT(*)::numeric * 100), 2) as win_rate_pct,
    ROUND(AVG(CASE WHEN is_win = 1 THEN pnl_pct END)::numeric * 100, 2) as avg_win_pct,
    ROUND(AVG(CASE WHEN is_win = 0 THEN ABS(pnl_pct) END)::numeric * 100, 2) as avg_loss_pct,
    ROUND((AVG(CASE WHEN is_win = 1 THEN pnl_pct END) /
           ABS(AVG(CASE WHEN is_win = 0 THEN pnl_pct END)))::numeric, 2) as profit_loss_ratio
FROM trades;
```

### 4. 查看当前持仓

```sql
-- 查看当前所有持仓
SELECT
    symbol,
    ROUND(quantity::numeric, 2) as qty,
    ROUND(cost_price::numeric, 2) as cost,
    ROUND(market_value::numeric, 0) as market_val,
    ROUND(unrealized_pnl::numeric, 0) as unrealized_pnl,
    ROUND((unrealized_pnl / (cost_price * quantity) * 100)::numeric, 2) as pnl_pct,
    to_char(updated_at, 'YYYY-MM-DD HH24:MI') as updated
FROM positions
WHERE quantity > 0
ORDER BY market_value DESC;
```

### 5. 查看活跃止损止盈设置

```sql
-- 查看当前所有活跃的止损止盈
SELECT
    symbol,
    ROUND(entry_price::numeric, 2) as entry,
    ROUND(stop_loss::numeric, 2) as stop_loss,
    ROUND(take_profit::numeric, 2) as take_profit,
    strategy,
    status,
    to_char(created_at, 'MM-DD HH24:MI') as created
FROM position_stops
WHERE status = 'active'
ORDER BY created_at DESC;
```

### 6. 按市场统计交易表现

```sql
-- 港股 vs 美股交易表现对比
SELECT
    CASE
        WHEN symbol LIKE '%.HK' THEN 'HK'
        WHEN symbol LIKE '%.US' THEN 'US'
        ELSE 'OTHER'
    END as market,
    COUNT(*) as total_trades,
    SUM(CASE WHEN exit_price > entry_price THEN 1 ELSE 0 END) as wins,
    ROUND((SUM(CASE WHEN exit_price > entry_price THEN 1 ELSE 0 END)::numeric /
           COUNT(*)::numeric * 100), 2) as win_rate_pct,
    ROUND(AVG((exit_price - entry_price) / entry_price * 100)::numeric, 2) as avg_return_pct
FROM position_stops
WHERE exit_time >= NOW() - INTERVAL '30 days'
    AND status IN ('hit_stop_loss', 'hit_take_profit', 'closed')
    AND entry_price > 0
    AND exit_price > 0
GROUP BY market
ORDER BY total_trades DESC;
```

---

## 🛠️ 实用脚本

### 快速查询脚本

创建 `query_db.sh`:

```bash
#!/bin/bash
# PostgreSQL 快速查询脚本

DB_HOST="127.0.0.1"
DB_USER="postgres"
DB_NAME="longport_next_new"
DB_PASS="jinhua"

# 使用 PGPASSWORD 环境变量避免密码提示
export PGPASSWORD="$DB_PASS"

case "$1" in
    "orders")
        echo "📊 今日订单:"
        psql -h $DB_HOST -U $DB_USER -d $DB_NAME -c "SELECT symbol, side, ROUND(price::numeric, 2) as price, status, to_char(created_at, 'HH24:MI:SS') as time FROM orderrecord WHERE DATE(created_at) = CURRENT_DATE ORDER BY created_at DESC;"
        ;;
    "history")
        echo "📜 最近 30 天交易历史:"
        psql -h $DB_HOST -U $DB_USER -d $DB_NAME -c "SELECT symbol, ROUND(entry_price::numeric, 2) as entry, ROUND(exit_price::numeric, 2) as exit, ROUND(pnl::numeric, 2) as pnl, status, to_char(exit_time, 'MM-DD HH24:MI') as time FROM position_stops WHERE exit_time >= NOW() - INTERVAL '30 days' AND status IN ('hit_stop_loss', 'hit_take_profit', 'closed') ORDER BY exit_time DESC LIMIT 20;"
        ;;
    "stats")
        echo "📈 交易统计（凯利公式数据）:"
        psql -h $DB_HOST -U $DB_USER -d $DB_NAME -c "WITH trades AS (SELECT CASE WHEN exit_price > entry_price THEN 1 ELSE 0 END as is_win, (exit_price - entry_price) / entry_price as pnl_pct FROM position_stops WHERE exit_time >= NOW() - INTERVAL '30 days' AND status IN ('hit_stop_loss', 'hit_take_profit', 'closed') AND entry_price > 0 AND exit_price > 0) SELECT COUNT(*) as total, SUM(is_win) as wins, ROUND((SUM(is_win)::numeric / COUNT(*)::numeric * 100), 2) as win_rate, ROUND(AVG(CASE WHEN is_win = 1 THEN pnl_pct END)::numeric * 100, 2) as avg_win, ROUND(AVG(CASE WHEN is_win = 0 THEN ABS(pnl_pct) END)::numeric * 100, 2) as avg_loss FROM trades;"
        ;;
    "positions")
        echo "💼 当前持仓:"
        psql -h $DB_HOST -U $DB_USER -d $DB_NAME -c "SELECT symbol, ROUND(quantity::numeric, 2) as qty, ROUND(cost_price::numeric, 2) as cost, ROUND(market_value::numeric, 0) as value, ROUND((unrealized_pnl / (cost_price * quantity) * 100)::numeric, 2) as pnl_pct FROM positions WHERE quantity > 0 ORDER BY market_value DESC;"
        ;;
    "stops")
        echo "🛑 活跃止损止盈:"
        psql -h $DB_HOST -U $DB_USER -d $DB_NAME -c "SELECT symbol, ROUND(entry_price::numeric, 2) as entry, ROUND(stop_loss::numeric, 2) as sl, ROUND(take_profit::numeric, 2) as tp, status FROM position_stops WHERE status = 'active' ORDER BY created_at DESC;"
        ;;
    *)
        echo "用法: $0 {orders|history|stats|positions|stops}"
        echo ""
        echo "  orders     - 查看今日订单"
        echo "  history    - 查看交易历史（30天）"
        echo "  stats      - 查看交易统计（胜率、盈亏比）"
        echo "  positions  - 查看当前持仓"
        echo "  stops      - 查看活跃止损止盈"
        ;;
esac

unset PGPASSWORD
```

使用方式：

```bash
chmod +x query_db.sh

./query_db.sh orders     # 今日订单
./query_db.sh history    # 交易历史
./query_db.sh stats      # 交易统计
./query_db.sh positions  # 当前持仓
./query_db.sh stops      # 止损止盈
```

---

## 📊 监控仪表板查询

### 实时监控

```sql
-- 创建实时监控视图
CREATE OR REPLACE VIEW trading_dashboard AS
SELECT
    -- 今日订单统计
    (SELECT COUNT(*) FROM orderrecord WHERE DATE(created_at) = CURRENT_DATE) as today_orders,
    (SELECT COUNT(*) FROM orderrecord WHERE DATE(created_at) = CURRENT_DATE AND side = 'Buy') as today_buys,
    (SELECT COUNT(*) FROM orderrecord WHERE DATE(created_at) = CURRENT_DATE AND side = 'Sell') as today_sells,

    -- 当前持仓统计
    (SELECT COUNT(*) FROM positions WHERE quantity > 0) as total_positions,
    (SELECT SUM(market_value) FROM positions WHERE quantity > 0) as total_market_value,
    (SELECT SUM(unrealized_pnl) FROM positions WHERE quantity > 0) as total_unrealized_pnl,

    -- 30天交易统计
    (SELECT COUNT(*) FROM position_stops
     WHERE exit_time >= NOW() - INTERVAL '30 days'
     AND status IN ('hit_stop_loss', 'hit_take_profit', 'closed')) as trades_30d,

    (SELECT COUNT(*) FROM position_stops
     WHERE exit_time >= NOW() - INTERVAL '30 days'
     AND status IN ('hit_stop_loss', 'hit_take_profit', 'closed')
     AND exit_price > entry_price) as wins_30d;

-- 查询仪表板
SELECT * FROM trading_dashboard;
```

---

## ⚠️ 重要提示

1. **数据库类型**: 系统使用 **PostgreSQL**，不是 SQLite
2. **主要表**:
   - `orderrecord` - 订单记录
   - `position_stops` - 交易历史（进场/出场价格）
   - `positions` - 当前持仓
3. **凯利公式数据源**: 从 `position_stops` 表读取已平仓交易
4. **时区**: 数据库时间戳使用 UTC+8（北京时间）

---

## 🔧 故障排查

### 连接失败

```bash
# 检查 PostgreSQL 服务
sudo systemctl status postgresql

# 检查端口
netstat -an | grep 5432

# 测试连接
psql -h 127.0.0.1 -U postgres -d longport_next_new -c "SELECT NOW();"
```

### 查看表结构

```sql
-- 查看 position_stops 表结构（凯利公式数据源）
\d position_stops

-- 查看所有表
\dt

-- 查看表记录数
SELECT
    schemaname,
    tablename,
    (SELECT COUNT(*) FROM position_stops) as position_stops_count,
    (SELECT COUNT(*) FROM orderrecord) as orderrecord_count,
    (SELECT COUNT(*) FROM positions WHERE quantity > 0) as active_positions_count;
```

---

这份文档提供了所有必要的数据库查询命令，用于监控和分析系统运行状态。
