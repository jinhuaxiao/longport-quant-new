# 🚀 快速启动指南

## 选择合适的交易策略

系统提供了两个版本的技术指标交易策略，请根据需求选择：

### 📊 版本对比

| 特性 | 基础版 v1.0 | 高级版 v2.0 |
|------|------------|------------|
| **复杂度** | 简单 | 复杂 |
| **技术指标** | RSI + 布林带 | RSI + 布林带 + MACD + 成交量 + ATR |
| **信号生成** | 简单阈值 | 智能评分(0-100) |
| **止损止盈** | ❌ 仅记录 | ✅ 自动执行 |
| **适合人群** | 学习者、测试 | 实际交易 |
| **推荐资金** | < $10,000 | > $10,000 |

---

## 🎯 基础版 (v1.0) - 快速开始

### 特点
- ✅ 简单易懂
- ✅ 快速上手
- ✅ 适合学习技术分析基础

### 启动命令

```bash
# 1. 配置监控股票
nano configs/watchlist.yml

# 2. 运行系统
python3 scripts/technical_indicator_trading.py

# 3. 查看输出
# 系统会每60秒扫描一次，显示交易信号
```

### 主要功能
- RSI超卖/超买检测
- 布林带价格位置分析
- 简单的买入信号生成

### 查看详细文档
```bash
cat TECHNICAL_INDICATOR_STRATEGY.md
```

---

## 🎖️ 高级版 (v2.0) - 完整交易系统

### 特点
- ✅ 多指标综合分析
- ✅ 智能评分系统
- ✅ 自动止损止盈
- ✅ 完整风险管理

### 启动命令

```bash
# 1. 配置监控股票
nano configs/watchlist.yml

# 2. 后台运行（推荐）
screen -S trading
python3 scripts/advanced_technical_trading.py

# 按 Ctrl+A 然后 D 退出screen
# screen -r trading 重新连接

# 3. 查看运行日志
tail -f trading.log
```

### 主要功能
- 📊 6种技术指标综合分析
- 🎯 智能评分系统(0-100分)
- 🛑 ATR动态止损止盈
- 🔄 自动平仓管理
- 📈 多周期趋势确认
- 📊 成交量放量确认

### 查看详细文档
```bash
cat ADVANCED_STRATEGY_GUIDE.md
```

---

## ⚙️ 通用配置

### 1. 配置API密钥

编辑 `.env` 文件:
```bash
# 长桥API配置
LONGPORT_APP_KEY=你的APP_KEY
LONGPORT_APP_SECRET=你的APP_SECRET
LONGPORT_ACCESS_TOKEN=你的ACCESS_TOKEN
```

### 2. 配置监控股票

编辑 `configs/watchlist.yml`:
```yaml
markets:
  hk:
    - 09988.HK  # 阿里巴巴
    - 03690.HK  # 美团
    - 01810.HK  # 小米

  us:
    - AAPL.US   # 苹果
    - MSFT.US   # 微软
    - NVDA.US   # 英伟达

symbols: []
```

### 3. 调整交易参数

#### 基础版参数
编辑 `scripts/technical_indicator_trading.py`:
```python
self.budget_per_stock = 5000    # 每只股票预算
self.max_positions = 5          # 最大持仓数
self.rsi_period = 14            # RSI周期
self.rsi_oversold = 30          # RSI超卖阈值
self.bb_period = 20             # 布林带周期
```

#### 高级版参数
编辑 `scripts/advanced_technical_trading.py`:
```python
# 基础参数同上，额外增加:
self.macd_fast = 12                 # MACD快线
self.volume_surge_threshold = 1.5   # 放量阈值
self.atr_stop_multiplier = 2.0      # 止损倍数
self.use_dynamic_stops = True       # 启用动态止损
```

---

## 📊 实际运行示例

### 基础版输出

```
============================================================
启动实时自动交易系统
策略：RSI(14) + 布林带(20, 2.0σ)
============================================================

✅ 监控 5 个标的: ['09988.HK', '03690.HK', '01810.HK', 'AAPL.US', 'MSFT.US']

账户状态:
  💰 余额: HKD $100,000.00
  📦 持仓: 0/5

第 1 轮扫描 - 14:35:22
============================================================
📊 获取到 5 个标的的实时行情

🎯 03690.HK 生成交易信号:
   类型: STRONG_BUY
   强度: 90.0%
   价格: $102.30
   RSI: 28.5
   布林带位置: 15.2%
   原因: RSI超卖(28.5) + 触及布林带下轨

✅ 订单已提交: 987654321
   BUY 03690.HK 48股 @ $102.30
   总额: $4,910.40

⏳ 等待60秒进入下一轮...
```

### 高级版输出

```
======================================================================
启动高级技术指标自动交易系统
策略组合: RSI(14) + BB(20,2.0σ) + MACD + Volume + ATR
======================================================================

✅ 监控 5 个标的

📈 账户状态:
  💰 HKD 余额: $100,000.00
  📦 持仓数: 0/5

第 1 轮扫描 - 14:35:22
======================================================================
📊 获取到 5 个标的的实时行情

🎯 03690.HK 生成交易信号:
   类型: STRONG_BUY
   综合评分: 85/100
   当前价格: $102.30
   RSI: 28.5
   布林带位置: 15.2% (宽度: 8.5%)
   MACD: 0.152
   成交量比率: 2.30x
   ATR: $3.25
   趋势: bullish
   止损位: $95.80 (-6.4%)
   止盈位: $112.05 (+9.5%)
   原因: RSI超卖(28.5), 触及布林带下轨, MACD金叉, 成交量大幅放大(2.3x),
         SMA20在SMA50上方(上升趋势)

✅ 开仓订单已提交: 987654321
   标的: 03690.HK
   类型: STRONG_BUY
   评分: 85/100
   数量: 49股
   价格: $102.30
   总额: $5,012.70
   止损位: $95.80
   止盈位: $112.05

⏳ 等待60秒进入下一轮...
```

---

## 🛠️ 常用命令

### 启动系统

```bash
# 基础版 - 前台运行
python3 scripts/technical_indicator_trading.py

# 高级版 - 前台运行
python3 scripts/advanced_technical_trading.py

# 高级版 - 后台运行（推荐）
nohup python3 scripts/advanced_technical_trading.py > trading.log 2>&1 &
```

### 监控系统

```bash
# 实时查看日志
tail -f trading.log

# 搜索交易信号
grep "生成交易信号" trading.log

# 搜索订单执行
grep "订单已提交" trading.log

# 查看止损止盈
grep "触及止" trading.log

# 统计交易次数
grep "订单已提交" trading.log | wc -l
```

### 停止系统

```bash
# 前台运行: 按 Ctrl+C

# 后台运行:
pkill -f technical_indicator_trading.py    # 停止基础版
pkill -f advanced_technical_trading.py     # 停止高级版

# 使用screen:
screen -r trading    # 重新连接
# 然后按 Ctrl+C 停止
```

---

## 📝 检查清单

在正式运行前，请确认：

### ✅ 环境配置
- [ ] Python 3.8+ 已安装
- [ ] 依赖包已安装 (`pip install -r requirements.txt`)
- [ ] 长桥API密钥已配置
- [ ] 数据库连接正常

### ✅ 策略配置
- [ ] 已选择合适的版本（基础版或高级版）
- [ ] 监控股票列表已配置
- [ ] 交易参数已调整
- [ ] 预算和持仓限制已设置

### ✅ 风险管理
- [ ] 使用模拟盘测试（**强烈推荐**）
- [ ] 小资金开始（建议不超过总资金的10%）
- [ ] 理解止损机制
- [ ] 准备好应急预案

### ✅ 监控准备
- [ ] 知道如何查看日志
- [ ] 知道如何停止系统
- [ ] 设置了账户异常提醒
- [ ] 准备定期检查持仓

---

## ⚠️ 重要提醒

### 1. **模拟盘测试**
```bash
# 先在模拟盘运行至少2周
# 观察以下指标:
- 信号质量和频率
- 胜率和盈亏比
- 最大回撤
- 系统稳定性
```

### 2. **小资金开始**
```bash
# 第一次使用建议:
initial_capital = $5,000 - $10,000
budget_per_stock = $1,000 - $2,000
max_positions = 3 - 5
```

### 3. **定期检查**
```bash
# 每天检查:
- 持仓情况
- 盈亏状态
- 系统日志

# 每周分析:
- 交易胜率
- 平均收益
- 策略有效性
```

### 4. **风险控制**
```bash
# 设置总体风险限制:
- 单日最大亏损: 总资金的2-5%
- 单只股票最大亏损: 预算的5-10%
- 总持仓比例: 不超过总资金的50%
```

---

## 📚 进阶学习

### 1. 理解技术指标
```bash
# 推荐阅读
- RSI指标详解: docs/indicators/rsi.md
- 布林带详解: docs/indicators/bollinger_bands.md
- MACD详解: docs/indicators/macd.md
- ATR详解: docs/indicators/atr.md
```

### 2. 策略优化
```bash
# 回测历史数据
python3 scripts/run_backtest.py \
  --strategy advanced_technical \
  --start-date 2024-01-01 \
  --end-date 2024-12-31

# 参数优化
python3 scripts/optimize_parameters.py \
  --strategy advanced_technical \
  --param rsi_oversold \
  --range 25,35
```

### 3. 查看详细文档
```bash
# 基础版详细文档
cat TECHNICAL_INDICATOR_STRATEGY.md

# 高级版详细文档
cat ADVANCED_STRATEGY_GUIDE.md

# 版本对比
cat STRATEGY_COMPARISON.md
```

---

## 🆘 遇到问题？

### 常见问题

#### Q1: 获取不到行情数据
```bash
原因: 不在交易时段或网络问题
解决:
  - 检查当前时间是否在交易时段
  - 测试网络连接: ping api.longportapp.com
  - 检查API密钥是否正确
```

#### Q2: 订单提交失败
```bash
原因: 资金不足或API限流
解决:
  - 查看账户余额
  - 减少budget_per_stock参数
  - 检查API调用频率
```

#### Q3: 系统突然停止
```bash
原因: 异常错误或API连接中断
解决:
  - 查看日志: tail -50 trading.log
  - 重启系统
  - 如果持续出现，联系技术支持
```

### 获取帮助

```bash
# 查看系统状态
python3 scripts/system_status.py

# 运行诊断
python3 scripts/diagnose.py

# 查看GitHub Issues
https://github.com/your-repo/issues
```

---

## 🎯 推荐流程

### 第1步: 模拟盘测试 (1-2周)
```bash
1. 使用基础版熟悉系统
2. 观察信号生成逻辑
3. 理解技术指标含义
```

### 第2步: 小资金实盘 (2-4周)
```bash
1. 切换到高级版
2. 投入少量资金($5,000-$10,000)
3. 每天检查运行状况
4. 记录和分析结果
```

### 第3步: 参数优化 (持续)
```bash
1. 根据实际表现调整参数
2. 测试不同的股票组合
3. 优化风险管理策略
```

### 第4步: 扩大规模 (谨慎)
```bash
1. 确认策略长期有效
2. 逐步增加投入资金
3. 保持严格的风险控制
```

---

## 📞 技术支持

- **文档**: 查看 `docs/` 目录
- **示例**: 查看 `examples/` 目录
- **问题**: 提交 GitHub Issue
- **讨论**: 加入交流群

---

**祝交易成功！** 🚀

记住: **风险管理永远是第一位的！**