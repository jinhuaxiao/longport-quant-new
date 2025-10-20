# LongPort API 配额管理指南

## 问题描述

错误码 `301607` 表示 **历史K线API配额已用尽**。LongPort 对历史K线接口有每日请求次数限制。

## 解决方案

### 1. 等待配额重置 ⏰（推荐）

- **配额重置时间**：每天 UTC+8 00:00
- **适用场景**：非紧急数据同步

### 2. 使用实时数据API作为后备 🚀

当历史K线配额用尽时，可以使用实时行情API同步**当天**的数据（不占用历史K线配额）：

```bash
# 同步指定股票的今日数据
python3 scripts/sync_realtime_fallback.py TQQQ.US NVDU.US RKLB.US HOOD.US

# 或者批量同步watchlist中的股票
python3 scripts/sync_realtime_fallback.py $(python3 -c "from longport_quant.data.watchlist import WatchlistLoader; print(' '.join(list(WatchlistLoader().load().symbols('us'))[:10]))")
```

**限制**：
- ✅ 可以获取当天的实时数据
- ❌ 无法获取历史数据（仍需等待配额重置）

### 3. 优化同步策略 📊

#### 减小批次大小和增加延迟

已优化的配置（`scripts/sync_historical_klines.py`）：

```python
# 每批只处理 2 个股票
--batch-size 2

# 批次间延迟 5 秒
# 股票间延迟 0.5 秒
```

#### 优先同步重要股票

```bash
# 只同步watchlist中优先级高的股票
python3 scripts/sync_historical_klines.py --limit 10 --years 1
```

#### 分市场同步

```bash
# 今天同步美股
python3 scripts/sync_historical_klines.py --market us --years 1

# 明天同步港股
python3 scripts/sync_historical_klines.py --market hk --years 1
```

## 智能配额检测

系统已添加智能配额检测：

- 当遇到 `301607` 错误时，**自动停止后续同步**
- 避免浪费无效的 API 调用
- 提供明确的错误提示和建议

## 最佳实践 ✨

### 日常数据同步策略

```bash
# 1. 每天定时任务（凌晨 1 点）- 同步历史数据
0 1 * * * cd /data/web/longport-quant-new && python3 scripts/sync_historical_klines.py --years 1 --batch-size 2

# 2. 交易时段（盘中）- 使用实时数据
*/30 9-16 * * 1-5 cd /data/web/longport-quant-new && python3 scripts/sync_realtime_fallback.py TQQQ.US NVDU.US
```

### 避免配额耗尽

1. **减少同步频率**：日线数据每天同步 1 次即可
2. **减小批次大小**：`--batch-size 2` 或更小
3. **增加延迟**：批次间延迟 5 秒以上
4. **分时同步**：不同市场分开在不同时间同步
5. **使用实时API**：盘中数据用实时API，历史数据夜间同步

## 监控配额使用

```bash
# 查看今天的配额使用情况
grep "301607" scheduler_*.log | wc -l

# 查看今天成功同步的次数
grep "Synced.*daily K-line" scheduler_*.log | wc -l
```

## 配额限制参考

根据经验值（可能因订阅等级不同）：

| API类型 | 每日限额（估计） | 备注 |
|---------|------------------|------|
| 历史日线 | ~200-500 次 | 每个symbol算一次 |
| 历史分钟线 | ~100-200 次 | 更严格 |
| 实时行情 | 更宽松 | 不限制（但有频率限制） |

**注意**：这些数字是根据观察得出的经验值，实际限制以 LongPort 官方文档为准。

## 故障排查

### 问题：仍然遇到 301607 错误

**检查**：
1. 是否今天已经同步过多次？
2. 是否有其他进程在使用相同的 API key？
3. 配额是否已在其他应用中用尽？

**解决**：
- 等待第二天配额重置
- 使用 `sync_realtime_fallback.py` 同步今日数据
- 联系 LongPort 确认配额限制

### 问题：实时数据同步失败

**可能原因**：
- 市场未开盘（无实时数据）
- 股票代码错误
- 网络连接问题

**解决**：
- 检查市场交易时段
- 验证股票代码格式（如 `TQQQ.US`）
- 检查 API 连接状态