# 长桥量化交易系统 - 文档中心

本目录包含项目的所有技术文档，按主题分类整理。

## 📁 目录结构

### 🏗️ architecture/ - 架构设计
系统架构、技术方案和设计文档

- `ARCHITECTURE.md` - 系统整体架构
- `AUTO_TRADING_ARCHITECTURE.md` - 自动交易架构详解
- `QUANT_FRONTEND_TECHNICAL_PROPOSAL.md` - 前端技术方案（Next.js）
- `REDIS_QUEUE_MIGRATION.md` - Redis 队列迁移方案

### 📖 guides/ - 用户指南
快速入门、使用指南和教程

- `QUICK_START.md` - 快速开始
- `QUICK_START_AUTO_TRADING.md` - 自动交易快速入门
- `QUICK_START_QUEUE_SYSTEM.md` - 队列系统快速入门
- `QUICK_START_SIGNAL_OPTIMIZATION.md` - 信号优化快速入门
- `ADVANCED_STRATEGY_GUIDE.md` - 高级策略指南
- `SLACK_SETUP_QUICKSTART.md` - Slack 通知设置
- `QUEUE_CLEANUP_GUIDE.md` - 队列清理指南
- `DYNAMIC_STOP_LOSS_QUICKSTART.md` - 动态止损快速入门

### 🔧 implementation/ - 功能实现
核心功能的实现文档和技术细节

- `IMPLEMENTATION_SUMMARY.md` - 实现总结
- `IMPLEMENTATION_SLACK_NOTIFICATIONS.md` - Slack 通知实现
- `STOP_LOSS_STRATEGY.md` - 止损策略实现
- `STOP_LOSS_SYSTEM_EXPLAINED.md` - 止损系统详解
- `SIGNAL_DEDUPLICATION.md` - 信号去重实现
- `SIGNAL_GENERATOR_OPTIMIZATIONS.md` - 信号生成器优化
- `DYNAMIC_STOP_LOSS_DESIGN.md` - 动态止损设计
- `DYNAMIC_STOP_LOSS_IMPLEMENTATION.md` - 动态止损实现
- `HYBRID_STRATEGY_UPGRADE.md` - 混合策略升级
- `HOW_SIGNALS_ARE_PROCESSED.md` - 信号处理流程
- `SMART_POSITION_ROTATION.md` - 智能仓位轮换
- `MARKET_AWARE_UPDATE.md` - 市场感知更新
- `API_QUOTA_MANAGEMENT.md` - API 配额管理
- `BUILTIN_WATCHLIST.md` - 内置监视列表
- `MARKET_AWARE_TRADING.md` - 市场感知交易
- `SLACK_NOTIFICATION.md` - Slack 通知
- `SMART_POSITION_MANAGEMENT.md` - 智能仓位管理

### 🔨 fixes/ - 问题修复
历史问题修复和诊断报告

- `ALL_FIXES_SUMMARY.md` - 所有修复总结
- `FD_LEAK_COMPLETE_FIX.md` - 文件描述符泄漏完整修复
- `FD_LEAK_FIX_SUMMARY.md` - 文件描述符泄漏修复总结
- `SIGNAL_EXECUTION_FIX.md` - 信号执行修复
- `CRITICAL_BUG_FIX_SIGNAL_DELETION.md` - 信号删除严重bug修复
- `BUGFIX_ETF_ANALYSIS.md` - ETF 分析 bug 修复
- `ANALYSIS_LOGGING_FIX.md` - 分析日志修复
- `ERROR_FIX_SUMMARY.md` - 错误修复总结
- `FUND_CHECK_FIX_SUMMARY.md` - 资金检查修复
- `PRIORITY_QUEUE_FIX.md` - 优先级队列修复
- `FIXES_SUMMARY.md` - 修复总结
- `FIX_CLIENT_INITIALIZATION.md` - 客户端初始化修复
- `FINAL_FIX_SUMMARY.md` - 最终修复总结
- `FINAL_DIAGNOSIS_AND_SOLUTION.md` - 最终诊断和解决方案
- `STOP_LOSS_DIAGNOSIS_REPORT.md` - 止损诊断报告
- `SIGNAL_QUEUE_AUTO_RECOVERY.md` - 信号队列自动恢复
- `CONCURRENT_OPTIMIZATION.md` - 并发优化
- `ENHANCEMENT_SUMMARY_20250930.md` - 增强功能总结

### 🚀 deployment/ - 部署相关
部署指南和配置说明

- `DEPLOYMENT.md` - 部署指南

### 📝 session-notes/ - 会话记录
开发会话记录和项目状态

- `SESSION_SUMMARY_20250930.md` - 2025-09-30 会话总结
- `AUTO_TRADING_STATUS.md` - 自动交易状态
- `DELIVERABLES.md` - 交付物清单
- `PROJECT_STATUS.md` - 项目状态

### 📦 miscellaneous/ - 其他文档
其他技术文档和参考资料

- `AGENTS.md` - Agent 相关
- `README_TRADING_SYSTEM.md` - 交易系统 README
- `REALTIME_TRADING_FINAL.md` - 实时交易最终版
- `REALTIME_TRADING_UPGRADE.md` - 实时交易升级
- `STRATEGY_COMPARISON.md` - 策略对比
- `TECHNICAL_INDICATOR_STRATEGY.md` - 技术指标策略
- `TODO.md` - 待办事项
- `TRADING_DAY_FIX.md` - 交易日修复
- `TRADING_SYSTEM_FIX_COMPLETE.md` - 交易系统完整修复
- `UNLIMITED_POSITIONS.md` - 无限持仓
- `UNLIMITED_POSITIONS_FIX.md` - 无限持仓修复
- `URGENT_FIX_SUMMARY.md` - 紧急修复总结
- `V2_REALTIME_TRADING_SUCCESS.md` - V2 实时交易成功
- `V2_SIGNAL_DEDUPLICATION_COMPLETE.md` - V2 信号去重完成
- `WATCHLIST_MODES.md` - 监视列表模式
- `WHY_NO_ORDERS.md` - 为什么没有订单

---

## 🔍 快速查找

### 新手入门
1. 从 `guides/QUICK_START.md` 开始
2. 了解自动交易：`guides/QUICK_START_AUTO_TRADING.md`
3. 查看系统架构：`architecture/AUTO_TRADING_ARCHITECTURE.md`

### 开发者
1. 系统架构：`architecture/`
2. 功能实现：`implementation/`
3. 前端开发：`architecture/QUANT_FRONTEND_TECHNICAL_PROPOSAL.md`

### 运维人员
1. 部署指南：`deployment/DEPLOYMENT.md`
2. 问题诊断：`fixes/`

### 项目管理
1. 项目状态：`session-notes/PROJECT_STATUS.md`
2. 交付物：`session-notes/DELIVERABLES.md`

---

**文档维护**：文档应保持更新，新增文档请放入对应分类目录。
