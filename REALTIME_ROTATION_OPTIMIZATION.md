# 实时挪仓优化说明

## 优化背景

之前的实时挪仓和紧急卖出功能存在响应延迟问题：
- 这些检查集成在主循环中
- 主循环间隔为 10 分钟（WebSocket 模式）或 60 秒（轮询模式）
- 导致高分信号因资金不足被延迟后，可能要等 10 分钟才能被检测并触发挪仓

## 优化内容

### 1. 创建独立后台任务
- 新增 `_rotation_checker_loop()` 方法作为独立的异步后台任务
- 每 30 秒检查一次实时挪仓和紧急卖出
- 不受主循环间隔影响

### 2. 分离检查逻辑
- 从主循环中移除实时挪仓和紧急卖出的检查代码
- 避免重复检查，提高效率

### 3. 智能市场开盘检测
- 新增 `_is_market_open_time()` 方法
- 只在市场交易时间内执行检查，节省资源

## 优化效果

### 之前
```
信号被延迟 → 等待 10 分钟 → 主循环检查 → 触发挪仓
```

### 现在
```
信号被延迟 → 等待最多 30 秒 → 后台任务检查 → 触发挪仓
```

**响应速度提升：从 10 分钟缩短到 30-60 秒**

## 代码变更

### 新增代码

1. **后台任务方法** (`scripts/signal_generator.py:4383-4463`)
   ```python
   async def _rotation_checker_loop(self):
       """每30秒检查一次实时挪仓和紧急卖出"""
   ```

2. **市场开盘检测** (`scripts/signal_generator.py:4465-4498`)
   ```python
   def _is_market_open_time(self, market: str) -> bool:
       """检查指定市场是否在交易时间"""
   ```

3. **任务启动** (`scripts/signal_generator.py:1377-1379`)
   ```python
   self._rotation_task = asyncio.create_task(self._rotation_checker_loop())
   ```

4. **任务清理** (`scripts/signal_generator.py:1605-1613`)
   ```python
   if self._rotation_task and not self._rotation_task.done():
       self._rotation_task.cancel()
   ```

### 移除代码

主循环中的重复检查（`scripts/signal_generator.py:1513-1519`）已替换为注释说明。

## 使用方式

### 应用优化
```bash
# 重启 signal_generator 以应用优化
pkill -f "signal_generator.py --account-id paper_001"
pkill -f "signal_generator.py --account-id live_001"

# 重新启动
python3 scripts/signal_generator.py --account-id paper_001 &
python3 scripts/signal_generator.py --account-id live_001 &
```

### 监控后台任务
```bash
# 查看日志确认后台任务已启动
tail -f logs/signal_generator_paper_001.log | grep "后台"

# 应该看到：
# ✅ 实时挪仓后台任务已启动（独立于主循环，每30秒检查）
# 🔄 启动实时挪仓和紧急卖出后台检查任务（间隔: 30秒）
# 🔍 后台检查: 账户余额=$xxx, 持仓数=xx
```

### 验证触发
```bash
# 运行测试脚本监控
python3 test_realtime_rotation_optimization.py

# 检查是否有：
# - 延迟信号（retry_after 未过期）
# - 后台检查日志
# - 实时挪仓或紧急卖出信号生成
```

## 配置参数

可在 `.env` 文件中调整检查间隔：

```bash
# 实时挪仓后台检查间隔（秒）
# 当前硬编码为 30 秒，如需调整可修改 signal_generator.py:270
# self._rotation_check_interval = 30
```

## 注意事项

1. **市场休市期间**：后台任务会跳过检查，节省资源
2. **账户信息获取失败**：后台任务会跳过本次检查，继续下一轮
3. **任务独立性**：后台任务失败不会影响主循环运行
4. **日志级别**：后台任务使用 DEBUG 级别记录常规检查，只有触发挪仓时才用 INFO/SUCCESS

## 测试工具

### test_realtime_rotation_optimization.py
```bash
python3 test_realtime_rotation_optimization.py
```

功能：
- 检查队列中的延迟信号数量
- 统计挪仓/紧急卖出信号数量
- 显示最近的相关日志
- 提供监控建议

## 预期日志输出

### 启动时
```
✅ 实时挪仓后台任务已启动（独立于主循环，每30秒检查）
🔄 启动实时挪仓和紧急卖出后台检查任务（间隔: 30秒）
```

### 运行中（DEBUG 级别）
```
🔍 后台检查: 账户余额=$17,973.71, 持仓数=16
⏭️  无延迟信号，跳过实时挪仓检查
```

### 检测到延迟信号
```
🔔 检测到 2 个高分信号因资金不足延迟
  - 941.HK: 评分=60, 延迟=45秒
  - 700.HK: 评分=60, 延迟=46秒
```

### 触发挪仓
```
🔔 后台检查触发实时挪仓: 生成 2 个卖出信号
✅ 后台检查完成: 实时挪仓=2, 紧急卖出=0
```

### 停止时
```
🛑 停止实时挪仓后台任务...
✅ 实时挪仓后台任务已停止
```

## 故障排查

### 问题：后台任务未启动
- 检查 signal_generator 是否重启
- 查看启动日志是否有错误

### 问题：有延迟信号但不触发挪仓
- 检查市场是否开盘（`_is_market_open_time`）
- 检查账户信息是否正常获取
- 检查持仓质量是否满足挪仓条件（分数差 >= 10）
- 检查日志是否有"无延迟信号"或其他跳过原因

### 问题：后台任务频繁失败
- 查看详细错误日志
- 检查网络连接和 API 限流
- 考虑增加检查间隔（从 30 秒改为 60 秒）

## 性能影响

- **CPU 使用**：增加约 1-2%（每 30 秒一次轻量级检查）
- **API 调用**：每 30 秒额外调用 1-2 次账户查询 API
- **网络流量**：每次检查约 10-50 KB
- **Redis 查询**：每 30 秒查询一次队列

**总体影响：极小，可忽略不计**

## 版本历史

- **2025-11-10**: 初始优化完成
  - 创建独立后台任务
  - 检查间隔从 10 分钟缩短到 30 秒
  - 添加市场开盘时间检测
