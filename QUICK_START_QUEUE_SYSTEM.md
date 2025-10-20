# 快速启动指南 - Redis队列交易系统

## ✅ 前置检查

**1. Redis必须运行**
```bash
redis-cli ping
# 应返回: PONG
```

**2. 队列系统测试通过**
```bash
echo "y" | python3 scripts/test_queue_system.py
# 应看到: ✅ 优先级顺序正确: [65, 50, 35]
```

---

## 🚀 方式1：一键启动（推荐）

```bash
# 启动1个信号生成器 + 1个订单执行器
bash scripts/start_trading_system.sh

# 启动1个信号生成器 + 3个订单执行器（并发）
bash scripts/start_trading_system.sh 3
```

---

## 🔧 方式2：手动启动（调试用）

**终端1 - 信号生成器**
```bash
python3 scripts/signal_generator.py
```

**终端2 - 订单执行器**
```bash
python3 scripts/order_executor.py
```

**终端3 - 队列监控（可选）**
```bash
python3 scripts/queue_monitor.py
```

---

## 📊 监控和管理

### 查看队列状态
```bash
python3 scripts/queue_monitor.py
```

### 查看日志
```bash
# 信号生成器
tail -f logs/signal_generator.log

# 订单执行器
tail -f logs/order_executor_1.log
```

### 使用Redis CLI
```bash
# 查看队列长度
redis-cli ZCARD trading:signals

# 查看所有信号
redis-cli ZRANGE trading:signals 0 -1 WITHSCORES

# 清空队列（危险！）
redis-cli DEL trading:signals
```

---

## ⏹️ 停止系统

```bash
bash scripts/stop_trading_system.sh
```

---

## 🎯 预期效果

### 信号生成器日志
```
🔄 第 1 轮扫描开始
📊 分析 9992.HK (泡泡玛特)
  实时行情: 价格=$291.00
  📊 综合评分: 63/100
  ✅ 决策: 生成买入信号
✅ 信号已发送到队列: BUY, 评分=63
```

### 订单执行器日志
```
📥 收到信号: 9992.HK, 类型=STRONG_BUY, 评分=63
🔍 开始处理 9992.HK 的 STRONG_BUY 信号
✅ 开仓订单已提交: 116303991698937856
   标的: 9992.HK
   数量: 100股
   价格: $291.50
```

### 队列监控输出
```
📊 队列状态
  📥 待处理队列: 2 个信号
  ⚙️  处理中队列: 1 个信号
  📈 处理速率:   0.50 信号/秒

📋 待处理信号:
优先级   标的         类型         评分   排队时间
65       9992.HK      STRONG_BUY   65     5秒前
50       1810.HK      BUY          50     8秒前
```

---

## 🐛 常见问题

### 问题：Redis连接失败
```bash
# 检查Redis是否运行
redis-cli ping

# 检查配置
cat .env | grep REDIS_URL
# 应该是: REDIS_URL=redis://localhost:6379/0
```

### 问题：队列积压
```bash
# 启动更多executor实例
bash scripts/start_trading_system.sh 3

# 或手动启动
python3 scripts/order_executor.py &
python3 scripts/order_executor.py &
```

### 问题：没有生成信号
```bash
# 检查signal_generator日志
tail -f logs/signal_generator.log | grep "评分\|信号"

# 可能原因：
# 1. 市场数据不足（< 30天）
# 2. 评分未达到阈值（< 30分）
# 3. API限流
```

---

## 📝 配置调整

### 修改轮询间隔
编辑 `scripts/signal_generator.py`:
```python
self.poll_interval = 60  # 改为30秒更频繁
```

### 修改队列配置
编辑 `.env`:
```bash
SIGNAL_MAX_RETRIES=3          # 最大重试次数
SIGNAL_QUEUE_MAX_SIZE=1000    # 队列最大长度
ORDER_EXECUTOR_WORKERS=1      # executor实例数
```

---

## ✅ 系统健康检查

运行以下命令确保系统正常：

```bash
# 1. Redis连接
python3 scripts/test_redis_connection.py

# 2. 队列功能
echo "y" | python3 scripts/test_queue_system.py

# 3. 查看进程
ps aux | grep -E "signal_generator|order_executor"

# 4. 查看队列状态
python3 scripts/queue_monitor.py
```

---

**日期**: 2025-10-16
**状态**: ✅ 已验证通过
