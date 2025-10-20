# 长桥量化交易系统 - 量化研究回测平台技术方案

**版本**: v1.0
**日期**: 2025-01-20
**作者**: Technical Planning Team
**项目**: LongPort Quant Research & Backtesting Platform

---

## 📋 目录

- [1. 项目概述](#1-项目概述)
- [2. 核心工作流](#2-核心工作流)
- [3. 系统架构](#3-系统架构)
- [4. 技术栈选型](#4-技术栈选型)
- [5. 前端页面设计](#5-前端页面设计)
- [6. 后端系统设计](#6-后端系统设计)
- [7. 数据库设计](#7-数据库设计)
- [8. API 接口设计](#8-api-接口设计)
- [9. 核心功能实现](#9-核心功能实现)
- [10. 实施计划](#10-实施计划)
- [11. 风险与挑战](#11-风险与挑战)
- [12. 预期成果](#12-预期成果)

---

## 1. 项目概述

### 1.1 业务目标

开发一个完整的**量化研究和回测平台**，支持从标的选择、数据准备、策略配置、回测执行、结果分析到实盘部署的全流程。

### 1.2 核心功能

#### 功能清单

1. **标的管理**
   - 自选股组合管理
   - 快速搜索和选择
   - 组合保存和加载

2. **数据管理**
   - 历史数据回填（1-3年）
   - 数据质量监控
   - 批量数据同步

3. **策略回测**
   - 技术指标策略
   - 参数优化
   - 多策略对比

4. **因子分析**
   - 多因子分析
   - IC值计算
   - 因子归因

5. **机器学习**
   - 模型训练（随机森林、XGBoost、LSTM、Transformer）
   - 特征工程
   - 模型评估
   - 超参数优化

6. **结果展示**
   - 收益曲线可视化
   - 风险指标分析
   - 交易记录明细
   - 对比基准指数

7. **实盘部署**
   - 策略一键部署
   - 风控参数配置
   - 实时监控
   - 紧急停止

### 1.3 用户角色

| 角色 | 权限 | 主要功能 |
|------|------|---------|
| **交易员** | 监控 + 执行 | 实盘监控、手动干预、订单管理 |
| **分析师** | 研究 + 回测 | 策略研发、回测分析、因子分析 |
| **管理员** | 全部权限 | 系统配置、用户管理、风控参数 |
| **观察者** | 只读 | 查看数据、查看报表 |

---

## 2. 核心工作流

### 2.1 工作流程图

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ 1. 选择标的 │ -> │ 2. 数据准备 │ -> │ 3. 策略配置 │ -> │ 4. 提交任务 │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
                                                                 │
                                                                 v
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ 7.部署实盘  │ <- │ 6. 结果分析 │ <- │ 5. 查看进度 │ <- │  后台执行   │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
```

### 2.2 详细流程说明

#### Step 1: 选择标的
- 从证券池中选择单个或多个标的
- 可保存为自选组合（如"港股科技"、"美股大盘"）
- 支持按市场、行业、市值筛选
- 支持快速搜索（代码/名称）

#### Step 2: 数据准备
- 检查历史数据完整性
- 一键回填缺失数据（日线/分钟线）
- 数据范围：2022-01-01 至今（约1-3年）
- 显示数据状态和进度
- 数据质量监控（缺失值、异常值）

#### Step 3: 策略配置
- **策略类型选择**：
  - 技术指标策略（MA、RSI、MACD、布林带等）
  - 机器学习模型（随机森林、XGBoost、LSTM、Transformer）
  - 因子分析（动量、价值、质量因子）
- **参数配置**：
  - 策略参数（如MA周期、RSI阈值）
  - 回测参数（初始资金、佣金、滑点）
  - 仓位管理（等权重、风险平价、凯利公式）

#### Step 4: 提交任务
- 提交到 Celery 异步任务队列
- 返回任务ID
- 任务优先级设置
- 预估完成时间

#### Step 5: 查看进度
- 实时显示任务进度（0-100%）
- WebSocket 推送状态更新
- 显示当前执行阶段
- 支持任务取消

#### Step 6: 结果分析
- **关键指标**：
  - 总收益率、年化收益、夏普比率、最大回撤
  - 胜率、盈亏比、交易次数、持仓天数
- **可视化图表**：
  - 收益曲线（策略 vs 基准）
  - 回撤曲线
  - 月度收益柱状图
- **详细分析**：
  - 交易记录明细
  - 持仓分析
  - 因子归因
  - 风险分析
- **对比基准**：
  - 与恒生指数、标普500对比
  - Alpha、Beta 计算

#### Step 7: 部署实盘
- 一键部署到实盘系统
- 风控参数配置（止损、止盈、仓位限制）
- 实时监控运行状态
- 紧急停止按钮
- 参数在线调整

---

## 3. 系统架构

### 3.1 整体架构图

```
┌──────────────────────────────────────────────────────────────┐
│                         用户浏览器                            │
│  ┌──────────────────────────────────────────────────────┐   │
│  │         Next.js Frontend (React + TypeScript)        │   │
│  │  - 研究工作台  - 回测结果  - 数据管理  - 实盘控制   │   │
│  └──────────────────────────────────────────────────────┘   │
└────────────────────────┬─────────────────────────────────────┘
                         │ HTTP/WebSocket
                         v
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI 后端服务器                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │  REST API   │  │  WebSocket  │  │   认证授权  │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
└────────────┬──────────────┬──────────────┬──────────────────┘
             │              │              │
             v              v              v
┌─────────────────┐ ┌──────────────┐ ┌──────────────┐
│ PostgreSQL DB   │ │Redis + Celery│ │  Data APIs   │
│ - 持仓/订单     │ │ - 任务队列   │ │ - LongPort   │
│ - 回测结果      │ │ - 缓存       │ │ - 行情数据   │
│ - ML模型        │ │ - 会话       │ │              │
└─────────────────┘ └──────────────┘ └──────────────┘

┌──────────────────────────────────────────────────────────────┐
│                    Celery Worker 集群                         │
│  ┌─────────────────┐  ┌─────────────────┐                    │
│  │ 回测引擎 Worker │  │ ML训练 Worker   │                    │
│  │ - Backtrader    │  │ - scikit-learn  │                    │
│  │ - VectorBT      │  │ - XGBoost       │                    │
│  │ - 自研引擎      │  │ - PyTorch       │                    │
│  └─────────────────┘  └─────────────────┘                    │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 技术架构分层

#### Layer 1: 前端层 (Presentation Layer)
- **框架**: Next.js 14+ (App Router)
- **UI库**: shadcn/ui + Tailwind CSS
- **状态管理**: Zustand (客户端状态) + React Query (服务端状态)
- **图表**: Apache ECharts / TradingView Lightweight Charts
- **表格**: TanStack Table v8
- **实时通信**: WebSocket (原生 API)

#### Layer 2: API层 (API Gateway Layer)
- **框架**: FastAPI (已有)
- **认证**: JWT Token
- **限流**: Redis + SlowAPI
- **文档**: OpenAPI 3.0 (自动生成)
- **CORS**: 配置允许前端域名

#### Layer 3: 业务逻辑层 (Business Logic Layer)
- **任务编排**: Celery + Redis
- **回测引擎**: Backtrader / VectorBT
- **机器学习**: scikit-learn / XGBoost / PyTorch
- **因子计算**: pandas / NumPy
- **技术指标**: TA-Lib

#### Layer 4: 数据层 (Data Layer)
- **主数据库**: PostgreSQL 14+ (已有)
- **时序扩展**: TimescaleDB (可选)
- **缓存**: Redis 7+
- **消息队列**: Redis (Celery Broker)

---

## 4. 技术栈选型

### 4.1 前端技术栈

| 类别 | 技术选型 | 版本 | 理由 |
|-----|---------|------|------|
| **框架** | Next.js | 14+ | SSR/SSG支持、App Router、性能优异、SEO友好 |
| **语言** | TypeScript | 5.0+ | 类型安全、开发效率高、减少运行时错误 |
| **UI库** | shadcn/ui | Latest | 现代化、可定制、组件丰富、无运行时开销 |
| **样式** | Tailwind CSS | 3.4+ | 快速开发、响应式友好、可维护性好 |
| **状态管理** | Zustand | 4.x | 轻量、简单、TypeScript友好、无模板代码 |
| **服务端状态** | React Query | 5.x | 自动缓存、重试、轮询、乐观更新 |
| **图表** | Apache ECharts | 5.x | 功能强大、中文文档完善、定制性强 |
| **表格** | TanStack Table | 8.x | 高性能、功能完整、headless设计 |
| **表单** | React Hook Form | 7.x | 性能好、验证完善、与UI库集成良好 |
| **日期处理** | date-fns | 3.x | 轻量、Tree-shakable、函数式API |
| **WebSocket** | 原生 WebSocket API | - | 简单直接、无额外依赖 |
| **HTTP客户端** | fetch / axios | - | 标准API / 拦截器支持 |

### 4.2 后端技术栈

| 类别 | 技术选型 | 版本 | 理由 |
|-----|---------|------|------|
| **Web框架** | FastAPI | 0.109+ | 已有、性能高、异步支持、自动文档 |
| **任务队列** | Celery | 5.3+ | 成熟稳定、监控完善、分布式支持 |
| **消息代理** | Redis | 7.0+ | 高性能、持久化、多种数据结构 |
| **回测引擎** | Backtrader | 1.9+ | 功能完整、社区活跃、文档丰富 |
| **机器学习** | scikit-learn | 1.4+ | 经典算法完整、API统一 |
| **梯度提升** | XGBoost | 2.0+ | 高性能、特征重要性、GPU支持 |
| **深度学习** | PyTorch | 2.1+ | 灵活、研究友好、动态图 |
| **数值计算** | NumPy | 1.26+ | 标准工具、高性能 |
| **数据处理** | pandas | 2.1+ | 数据分析标准库 |
| **技术指标** | TA-Lib | 0.4+ | 金融技术指标库 |

### 4.3 数据库和中间件

| 类别 | 技术选型 | 版本 | 理由 |
|-----|---------|------|------|
| **关系数据库** | PostgreSQL | 14+ | 已有、功能强大、JSONB支持 |
| **时序扩展** | TimescaleDB | 2.x | 时序数据优化、与PG兼容 |
| **缓存** | Redis | 7.0+ | 高性能KV存储、发布订阅 |
| **消息队列** | Redis | 7.0+ | 与Celery集成、简化架构 |

### 4.4 开发工具

| 类别 | 工具 | 用途 |
|-----|------|------|
| **包管理** | pnpm / npm | 前端依赖管理 |
| **代码格式化** | Prettier | 代码风格统一 |
| **代码检查** | ESLint | 代码质量检查 |
| **类型检查** | TypeScript | 静态类型检查 |
| **测试框架** | Jest + Vitest | 单元测试 |
| **E2E测试** | Playwright | 端到端测试 |
| **API测试** | pytest | Python单元测试 |
| **容器化** | Docker | 开发环境一致性 |

---

## 5. 前端页面设计

### 5.1 页面结构总览

```
app/
├── (auth)/                    # 认证相关
│   ├── login/                # 登录页
│   └── register/             # 注册页
│
├── (dashboard)/               # 主应用（需认证）
│   ├── layout.tsx            # 主布局（侧边栏+顶栏）
│   │
│   ├── research/             # 📊 研究工作台
│   │   ├── workspace/        # 工作区（标的选择+配置）
│   │   ├── results/[id]/     # 回测结果详情
│   │   └── library/          # 策略案例库
│   │
│   ├── data/                 # 💾 数据管理
│   │   ├── manager/          # 数据管理中心
│   │   ├── backfill/         # 数据回填
│   │   └── quality/          # 数据质量监控
│   │
│   ├── ml/                   # 🤖 机器学习
│   │   ├── training/         # 模型训练
│   │   ├── models/           # 模型管理
│   │   └── features/         # 特征工程
│   │
│   ├── live/                 # 🚀 实盘交易
│   │   ├── deployment/       # 策略部署
│   │   ├── monitor/          # 实时监控
│   │   ├── positions/        # 持仓管理
│   │   └── orders/           # 订单管理
│   │
│   ├── analysis/             # 📈 绩效分析
│   │   ├── performance/      # 收益分析
│   │   ├── risk/             # 风险分析
│   │   └── reports/          # 报表导出
│   │
│   └── settings/             # ⚙️ 系统设置
│       ├── account/          # 账户设置
│       ├── symbols/          # 自选股管理
│       ├── strategies/       # 策略配置
│       └── risk/             # 风控参数
```

### 5.2 核心页面详细设计

#### 5.2.1 研究工作台 (`/research/workspace`)

**页面布局**:
```
┌────────────────────────────────────────────────────────────────┐
│ 顶部导航栏: Logo | 工作台 | 数据 | 机器学习 | 实盘 | 设置    │
├────────────┬──────────────────────────────┬────────────────────┤
│            │                              │                    │
│  左侧面板  │       中间配置区域            │    右侧任务队列    │
│  (25%)    │         (50%)                │       (25%)        │
│            │                              │                    │
│ 标的选择器 │  Step 1: 数据准备            │  ⏳ 运行中 (2)    │
│            │  Step 2: 策略选择            │  ✅ 已完成 (5)    │
│ ┌────────┐ │  Step 3: 参数配置            │  ❌ 失败 (0)      │
│ │搜索框  │ │  Step 4: 风控设置            │                    │
│ └────────┘ │                              │  [查看全部]        │
│            │  [🚀 开始回测]               │                    │
│ ✓ AAPL    │                              │                    │
│ ✓ 09988.HK│                              │                    │
│ ✓ 00700.HK│                              │                    │
│            │                              │                    │
│ 📁 我的组合│                              │                    │
│  - 港股科技│                              │                    │
│  - 美股大盘│                              │                    │
│            │                              │                    │
│ [+ 新建组合]│                             │                    │
└────────────┴──────────────────────────────┴────────────────────┘
```

**代码示例 - 标的选择器**:
```typescript
// components/research/SymbolSelector.tsx
import { useState } from 'react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { SearchIcon, PlusIcon, XIcon } from 'lucide-react'

interface SymbolSelectorProps {
  selected: string[]
  onChange: (symbols: string[]) => void
}

export function SymbolSelector({ selected, onChange }: SymbolSelectorProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const [portfolios, setPortfolios] = useState([
    { id: 1, name: '港股科技', symbols: ['09988.HK', '00700.HK', '03690.HK'] },
    { id: 2, name: '美股大盘', symbols: ['AAPL', 'MSFT', 'GOOGL'] },
  ])

  const handleRemove = (symbol: string) => {
    onChange(selected.filter(s => s !== symbol))
  }

  const loadPortfolio = (portfolio: Portfolio) => {
    onChange([...new Set([...selected, ...portfolio.symbols])])
  }

  return (
    <div className="h-full flex flex-col border-r">
      {/* 搜索框 */}
      <div className="p-4 border-b">
        <div className="relative">
          <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="搜索标的代码或名称..."
            className="pl-9"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
      </div>

      {/* 已选标的 */}
      <div className="flex-1 overflow-auto">
        <div className="px-4 py-2 text-sm font-medium text-muted-foreground">
          已选择 ({selected.length})
        </div>
        <div className="px-2">
          {selected.map(symbol => (
            <div
              key={symbol}
              className="flex items-center justify-between p-2 rounded-md hover:bg-accent group"
            >
              <div className="flex items-center gap-2">
                <div className="h-2 w-2 rounded-full bg-green-500" />
                <span className="font-mono text-sm">{symbol}</span>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6 opacity-0 group-hover:opacity-100"
                onClick={() => handleRemove(symbol)}
              >
                <XIcon className="h-4 w-4" />
              </Button>
            </div>
          ))}
        </div>
      </div>

      {/* 自选组合 */}
      <div className="border-t p-4">
        <div className="text-sm font-medium mb-2">我的组合</div>
        <div className="space-y-2">
          {portfolios.map(portfolio => (
            <div
              key={portfolio.id}
              className="flex items-center justify-between p-2 rounded-md hover:bg-accent cursor-pointer"
              onClick={() => loadPortfolio(portfolio)}
            >
              <div>
                <div className="text-sm font-medium">{portfolio.name}</div>
                <div className="text-xs text-muted-foreground">
                  {portfolio.symbols.length} 个标的
                </div>
              </div>
              <Badge variant="outline" className="text-xs">
                加载
              </Badge>
            </div>
          ))}
        </div>
        <Button variant="outline" size="sm" className="w-full mt-3">
          <PlusIcon className="h-4 w-4 mr-1" /> 新建组合
        </Button>
      </div>
    </div>
  )
}
```

**代码示例 - 配置向导**:
```typescript
// components/research/BacktestWizard.tsx
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { RocketIcon, ArrowLeftIcon, ArrowRightIcon } from 'lucide-react'
import { DataPrepStep } from './steps/DataPrepStep'
import { StrategySelectStep } from './steps/StrategySelectStep'
import { ParameterConfigStep } from './steps/ParameterConfigStep'
import { RiskControlStep } from './steps/RiskControlStep'

interface BacktestConfig {
  symbols: string[]
  startDate: Date
  endDate: Date
  frequency: '1d' | '1m' | '5m'
  strategy?: string
  strategyParams?: Record<string, any>
  initialCash?: number
  commission?: number
  slippage?: number
}

const STEPS = [
  { id: 1, title: '数据准备' },
  { id: 2, title: '策略选择' },
  { id: 3, title: '参数配置' },
  { id: 4, title: '风控设置' },
]

export function BacktestWizard() {
  const [step, setStep] = useState(1)
  const [config, setConfig] = useState<BacktestConfig>({
    symbols: [],
    startDate: new Date('2022-01-01'),
    endDate: new Date(),
    frequency: '1d',
  })

  const handleNext = () => {
    if (step < STEPS.length) {
      setStep(step + 1)
    }
  }

  const handlePrevious = () => {
    if (step > 1) {
      setStep(step - 1)
    }
  }

  const handleSubmit = async () => {
    // 提交回测任务
    const response = await fetch('/api/tasks/backtest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config)
    })

    const result = await response.json()
    console.log('任务已提交:', result.task_id)
  }

  return (
    <div className="h-full flex flex-col">
      {/* 步骤指示器 */}
      <div className="px-6 py-4 border-b">
        <div className="flex items-center justify-between">
          {STEPS.map((s, idx) => (
            <div key={s.id} className="flex items-center">
              <div className={cn(
                "flex items-center justify-center w-8 h-8 rounded-full border-2",
                step >= s.id
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-muted text-muted-foreground"
              )}>
                {s.id}
              </div>
              <div className="ml-2">
                <div className={cn(
                  "text-sm font-medium",
                  step >= s.id ? "text-foreground" : "text-muted-foreground"
                )}>
                  {s.title}
                </div>
              </div>
              {idx < STEPS.length - 1 && (
                <div className={cn(
                  "mx-4 h-0.5 w-12",
                  step > s.id ? "bg-primary" : "bg-muted"
                )} />
              )}
            </div>
          ))}
        </div>
      </div>

      {/* 配置表单 */}
      <div className="flex-1 overflow-auto px-6 py-4">
        {step === 1 && <DataPrepStep config={config} onChange={setConfig} />}
        {step === 2 && <StrategySelectStep config={config} onChange={setConfig} />}
        {step === 3 && <ParameterConfigStep config={config} onChange={setConfig} />}
        {step === 4 && <RiskControlStep config={config} onChange={setConfig} />}
      </div>

      {/* 操作按钮 */}
      <div className="border-t px-6 py-4 flex justify-between">
        <Button
          variant="outline"
          onClick={handlePrevious}
          disabled={step === 1}
        >
          <ArrowLeftIcon className="h-4 w-4 mr-2" />
          上一步
        </Button>

        {step < STEPS.length ? (
          <Button onClick={handleNext}>
            下一步
            <ArrowRightIcon className="h-4 w-4 ml-2" />
          </Button>
        ) : (
          <Button onClick={handleSubmit}>
            <RocketIcon className="h-4 w-4 mr-2" />
            开始回测
          </Button>
        )}
      </div>
    </div>
  )
}
```

#### 5.2.2 回测结果页面 (`/research/results/[id]`)

**页面布局**:
```
┌────────────────────────────────────────────────────────────────┐
│ ← 返回 | 任务名称: MA交叉-港股科技 | ✅ 已完成               │
├────────────────────────────────────────────────────────────────┤
│                     关键指标卡片区域                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐         │
│  │总收益率  │ │年化收益  │ │夏普比率  │ │最大回撤  │         │
│  │ +34.2%  │ │ +18.5%  │ │  1.85   │ │ -12.3%  │         │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘         │
├────────────────────────────────────────────────────────────────┤
│                       收益曲线图表                              │
│   (ECharts 可交互图表)                                         │
├────────────────────────────────────────────────────────────────┤
│ [交易记录] [持仓分析] [因子分析] [风险分析] [对比基准]          │
├────────────────────────────────────────────────────────────────┤
│                      详细分析内容                              │
├────────────────────────────────────────────────────────────────┤
│ [📥 导出PDF] [🔄 重新运行] [🚀 部署实盘] [💾 保存到案例库]    │
└────────────────────────────────────────────────────────────────┘
```

**代码示例 - 结果页面**:
```typescript
// app/(dashboard)/research/results/[id]/page.tsx
import { Suspense } from 'react'
import { notFound } from 'next/navigation'
import { MetricCard } from '@/components/results/MetricCard'
import { EquityCurveChart } from '@/components/results/EquityCurveChart'
import { TradeHistoryTable } from '@/components/results/TradeHistoryTable'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'

async function getBacktestResults(taskId: string) {
  const res = await fetch(`http://localhost:8000/api/tasks/results/${taskId}`)
  if (!res.ok) return null
  return res.json()
}

export default async function BacktestResultPage({
  params
}: {
  params: { id: string }
}) {
  const data = await getBacktestResults(params.id)

  if (!data) {
    notFound()
  }

  const { task, results } = data

  return (
    <div className="container mx-auto py-6 space-y-6">
      {/* 页头 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon">
            <ArrowLeftIcon className="h-4 w-4" />
          </Button>
          <div>
            <h1 className="text-3xl font-bold">{task.name}</h1>
            <p className="text-sm text-muted-foreground">
              {task.symbols.join(', ')} · {task.startDate} ~ {task.endDate}
            </p>
          </div>
        </div>
        <Badge variant="success">已完成</Badge>
      </div>

      {/* 关键指标卡片 */}
      <div className="grid grid-cols-4 gap-4">
        <MetricCard
          title="总收益率"
          value={`${results.totalReturn > 0 ? '+' : ''}${results.totalReturn.toFixed(2)}%`}
          trend={results.totalReturn > 0 ? 'up' : 'down'}
          icon={<TrendingUpIcon />}
        />
        <MetricCard
          title="年化收益"
          value={`${results.annualReturn > 0 ? '+' : ''}${results.annualReturn.toFixed(2)}%`}
          trend={results.annualReturn > 0 ? 'up' : 'down'}
          icon={<BarChartIcon />}
        />
        <MetricCard
          title="夏普比率"
          value={results.sharpeRatio.toFixed(2)}
          description="风险调整后收益"
          icon={<ActivityIcon />}
        />
        <MetricCard
          title="最大回撤"
          value={`${results.maxDrawdown.toFixed(2)}%`}
          trend="down"
          icon={<AlertTriangleIcon />}
        />
      </div>

      {/* 收益曲线图表 */}
      <Card>
        <CardHeader>
          <CardTitle>收益曲线</CardTitle>
        </CardHeader>
        <CardContent>
          <EquityCurveChart
            data={results.equityCurve}
            benchmark={results.benchmarkCurve}
          />
        </CardContent>
      </Card>

      {/* 详细分析标签页 */}
      <Tabs defaultValue="trades">
        <TabsList>
          <TabsTrigger value="trades">交易记录</TabsTrigger>
          <TabsTrigger value="positions">持仓分析</TabsTrigger>
          <TabsTrigger value="factors">因子分析</TabsTrigger>
          <TabsTrigger value="risk">风险分析</TabsTrigger>
          <TabsTrigger value="comparison">对比基准</TabsTrigger>
        </TabsList>

        <TabsContent value="trades">
          <TradeHistoryTable trades={results.tradeHistory} />
        </TabsContent>

        {/* 其他标签页内容... */}
      </Tabs>

      {/* 操作按钮 */}
      <div className="flex justify-end gap-2">
        <Button variant="outline">
          <DownloadIcon className="h-4 w-4 mr-2" />
          导出报告
        </Button>
        <Button variant="outline">
          <RefreshIcon className="h-4 w-4 mr-2" />
          重新运行
        </Button>
        <Button>
          <RocketIcon className="h-4 w-4 mr-2" />
          部署到实盘
        </Button>
      </div>
    </div>
  )
}
```

**代码示例 - 收益曲线图表**:
```typescript
// components/results/EquityCurveChart.tsx
'use client'

import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

interface EquityCurveChartProps {
  data: Array<{ date: string; value: number; drawdown: number }>
  benchmark?: Array<{ date: string; value: number }>
}

export function EquityCurveChart({ data, benchmark }: EquityCurveChartProps) {
  const chartRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!chartRef.current) return

    const chart = echarts.init(chartRef.current)

    const option = {
      tooltip: {
        trigger: 'axis',
        axisPointer: {
          type: 'cross'
        }
      },
      legend: {
        data: ['策略收益', '基准收益', '回撤']
      },
      grid: {
        left: '3%',
        right: '4%',
        bottom: '3%',
        containLabel: true
      },
      xAxis: {
        type: 'category',
        data: data.map(d => d.date),
        boundaryGap: false
      },
      yAxis: [
        {
          type: 'value',
          name: '收益率 (%)',
          position: 'left',
        },
        {
          type: 'value',
          name: '回撤 (%)',
          position: 'right',
          inverse: true
        }
      ],
      series: [
        {
          name: '策略收益',
          type: 'line',
          data: data.map(d => d.value),
          smooth: true,
          lineStyle: {
            width: 2,
            color: '#10b981'
          },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(16, 185, 129, 0.3)' },
              { offset: 1, color: 'rgba(16, 185, 129, 0.05)' }
            ])
          }
        },
        benchmark && {
          name: '基准收益',
          type: 'line',
          data: benchmark.map(d => d.value),
          lineStyle: {
            width: 1,
            type: 'dashed',
            color: '#94a3b8'
          }
        },
        {
          name: '回撤',
          type: 'line',
          yAxisIndex: 1,
          data: data.map(d => Math.abs(d.drawdown)),
          lineStyle: {
            width: 1,
            color: '#ef4444'
          },
          areaStyle: {
            color: 'rgba(239, 68, 68, 0.1)'
          }
        }
      ].filter(Boolean)
    }

    chart.setOption(option)

    // 响应式
    const handleResize = () => chart.resize()
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.dispose()
    }
  }, [data, benchmark])

  return <div ref={chartRef} className="w-full h-[400px]" />
}
```

---

## 6. 后端系统设计

### 6.1 Celery 任务系统

#### 6.1.1 任务定义

**回测任务**:
```python
# tasks/backtest.py
from celery import Celery, Task
from celery.result import AsyncResult
import backtrader as bt
from typing import Dict, Any
from datetime import datetime
from loguru import logger

app = Celery('longport_quant')
app.config_from_object('celeryconfig')

class CallbackTask(Task):
    """支持进度回调的任务基类"""

    def update_progress(self, progress: int, message: str = ""):
        """更新任务进度"""
        self.update_state(
            state='PROGRESS',
            meta={
                'progress': progress,
                'message': message,
                'timestamp': datetime.now().isoformat()
            }
        )

@app.task(bind=True, base=CallbackTask)
def run_backtest(self, task_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    执行回测任务

    Args:
        task_config: 回测配置
            - symbols: List[str] - 标的列表
            - strategy_type: str - 策略类型
            - strategy_params: Dict - 策略参数
            - start_date: str - 开始日期
            - end_date: str - 结束日期
            - initial_cash: float - 初始资金
            - commission: float - 佣金费率
            - slippage: float - 滑点

    Returns:
        回测结果字典
    """
    try:
        # 1. 加载历史数据
        self.update_progress(10, "加载历史数据...")
        data_feeds = load_historical_data(
            symbols=task_config['symbols'],
            start_date=task_config['start_date'],
            end_date=task_config['end_date'],
            frequency=task_config.get('frequency', '1d')
        )

        # 2. 初始化回测引擎
        self.update_progress(20, "初始化回测引擎...")
        cerebro = bt.Cerebro()

        # 设置初始资金
        cerebro.broker.setcash(task_config['initial_cash'])

        # 设置佣金
        cerebro.broker.setcommission(commission=task_config['commission'] / 100)

        # 添加策略
        strategy_class = get_strategy_class(task_config['strategy_type'])
        cerebro.addstrategy(strategy_class, **task_config['strategy_params'])

        # 添加数据
        for symbol, data in data_feeds.items():
            cerebro.adddata(data, name=symbol)

        # 添加分析器
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe')
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
        cerebro.addanalyzer(bt.analyzers.Returns, _name='returns')
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')

        # 3. 运行回测
        self.update_progress(30, "运行回测...")

        initial_value = cerebro.broker.getvalue()
        results = cerebro.run()
        strat = results[0]
        final_value = cerebro.broker.getvalue()

        self.update_progress(80, "计算回测指标...")

        # 4. 提取回测结果
        backtest_results = {
            'task_id': self.request.id,
            'symbols': task_config['symbols'],
            'strategy_name': task_config['strategy_type'],
            'start_date': task_config['start_date'],
            'end_date': task_config['end_date'],

            # 收益指标
            'initial_value': initial_value,
            'final_value': final_value,
            'total_return': (final_value - initial_value) / initial_value * 100,
            'annual_return': strat.analyzers.returns.get_analysis()['rnorm100'],

            # 风险指标
            'sharpe_ratio': strat.analyzers.sharpe.get_analysis().get('sharperatio', 0),
            'max_drawdown': strat.analyzers.drawdown.get_analysis()['max']['drawdown'],

            # 交易统计
            'total_trades': strat.analyzers.trades.get_analysis()['total']['total'],
            'win_rate': calculate_win_rate(strat.analyzers.trades.get_analysis()),

            # 详细数据
            'equity_curve': extract_equity_curve(strat),
            'trade_history': extract_trade_history(strat),

            'created_at': datetime.now().isoformat()
        }

        # 5. 保存结果到数据库
        self.update_progress(90, "保存回测结果...")
        save_backtest_results(backtest_results)

        self.update_progress(100, "回测完成!")

        return {
            'status': 'SUCCESS',
            'results': backtest_results
        }

    except Exception as e:
        logger.error(f"回测任务失败: {e}", exc_info=True)
        raise

@app.task(bind=True, base=CallbackTask)
def train_ml_model(self, task_config: Dict[str, Any]) -> Dict[str, Any]:
    """训练机器学习模型"""
    try:
        # 1. 特征工程
        self.update_progress(10, "特征工程...")
        features_df = engineer_features(
            symbols=task_config['symbols'],
            feature_types=task_config['features'],
            start_date=task_config['start_date'],
            end_date=task_config['end_date']
        )

        # 2. 数据预处理
        self.update_progress(20, "数据预处理...")
        from sklearn.model_selection import train_test_split
        X, y = prepare_training_data(features_df)
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.3)

        # 3. 训练模型
        self.update_progress(40, "训练模型...")
        model = get_model(task_config['model_type'], task_config['hyperparameters'])
        model.fit(X_train, y_train)

        # 4. 评估模型
        self.update_progress(85, "评估模型...")
        from sklearn.metrics import accuracy_score, precision_score, recall_score
        y_pred = model.predict(X_test)

        metrics = {
            'accuracy': accuracy_score(y_test, y_pred),
            'precision': precision_score(y_test, y_pred),
            'recall': recall_score(y_test, y_pred),
        }

        # 5. 保存模型
        self.update_progress(95, "保存模型...")
        import joblib
        model_file_path = f"/models/{self.request.id}.pkl"
        joblib.dump(model, model_file_path)

        self.update_progress(100, "训练完成!")

        return {
            'status': 'SUCCESS',
            'metrics': metrics,
            'model_path': model_file_path
        }

    except Exception as e:
        logger.error(f"ML训练任务失败: {e}", exc_info=True)
        raise
```

#### 6.1.2 Celery 配置

```python
# celeryconfig.py
broker_url = 'redis://localhost:6379/0'
result_backend = 'redis://localhost:6379/0'

task_serializer = 'json'
result_serializer = 'json'
accept_content = ['json']
timezone = 'Asia/Hong_Kong'
enable_utc = True

# 任务路由
task_routes = {
    'tasks.backtest.run_backtest': {'queue': 'backtest'},
    'tasks.backtest.train_ml_model': {'queue': 'ml'},
    'tasks.backtest.backfill_historical_data': {'queue': 'data'},
}

# 任务超时
task_time_limit = 3600  # 1小时
task_soft_time_limit = 3300  # 55分钟

# 并发设置
worker_concurrency = 4
worker_prefetch_multiplier = 1
```

### 6.2 FastAPI 端点

```python
# api/tasks.py
from fastapi import APIRouter, HTTPException, WebSocket
from pydantic import BaseModel
from typing import List, Dict, Any
from celery.result import AsyncResult
from tasks.backtest import run_backtest, train_ml_model
import asyncio

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

class BacktestTaskRequest(BaseModel):
    symbols: List[str]
    strategy_type: str
    strategy_params: Dict[str, Any]
    start_date: str
    end_date: str
    initial_cash: float = 100000
    commission: float = 0.03
    slippage: float = 0.1

@router.post("/backtest")
async def submit_backtest_task(request: BacktestTaskRequest):
    """提交回测任务"""
    try:
        task = run_backtest.apply_async(
            kwargs={'task_config': request.dict()},
            queue='backtest'
        )

        return {
            'task_id': task.id,
            'status': 'submitted',
            'message': '回测任务已提交'
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status/{task_id}")
async def get_task_status(task_id: str):
    """获取任务状态"""
    task_result = AsyncResult(task_id)

    if task_result.state == 'PENDING':
        response = {'task_id': task_id, 'status': 'pending', 'progress': 0}
    elif task_result.state == 'PROGRESS':
        response = {
            'task_id': task_id,
            'status': 'running',
            'progress': task_result.info.get('progress', 0),
            'message': task_result.info.get('message', '')
        }
    elif task_result.state == 'SUCCESS':
        response = {
            'task_id': task_id,
            'status': 'success',
            'progress': 100,
            'result': task_result.result
        }
    elif task_result.state == 'FAILURE':
        response = {
            'task_id': task_id,
            'status': 'failed',
            'error': str(task_result.info)
        }

    return response

@router.websocket("/ws/{task_id}")
async def websocket_task_progress(websocket: WebSocket, task_id: str):
    """WebSocket实时推送任务进度"""
    await websocket.accept()

    try:
        while True:
            task_result = AsyncResult(task_id)

            if task_result.state == 'PROGRESS':
                await websocket.send_json({
                    'status': 'running',
                    'progress': task_result.info.get('progress', 0),
                    'message': task_result.info.get('message', '')
                })
            elif task_result.state == 'SUCCESS':
                await websocket.send_json({
                    'status': 'success',
                    'progress': 100,
                    'result': task_result.result
                })
                break
            elif task_result.state == 'FAILURE':
                await websocket.send_json({
                    'status': 'failed',
                    'error': str(task_result.info)
                })
                break

            await asyncio.sleep(1)

    except Exception as e:
        pass
```

---

## 7. 数据库设计

### 7.1 扩展的数据库表

```sql
-- 回测任务表
CREATE TABLE backtest_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id VARCHAR(64) UNIQUE NOT NULL,
    task_type VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    progress INTEGER DEFAULT 0,
    config JSONB NOT NULL,
    error_message TEXT,
    created_by VARCHAR(64),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_backtest_tasks_status ON backtest_tasks(status);
CREATE INDEX idx_backtest_tasks_created_at ON backtest_tasks(created_at DESC);

-- 回测结果表
CREATE TABLE backtest_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID REFERENCES backtest_tasks(id) ON DELETE CASCADE,
    strategy_name VARCHAR(64) NOT NULL,
    symbols TEXT[] NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,

    -- 资金指标
    initial_value DECIMAL(18, 2) NOT NULL,
    final_value DECIMAL(18, 2) NOT NULL,

    -- 收益指标
    total_return DECIMAL(10, 4),
    annual_return DECIMAL(10, 4),
    cumulative_return DECIMAL(10, 4),

    -- 风险指标
    sharpe_ratio DECIMAL(6, 3),
    sortino_ratio DECIMAL(6, 3),
    max_drawdown DECIMAL(6, 3),
    volatility DECIMAL(6, 3),

    -- 交易统计
    total_trades INTEGER,
    win_rate DECIMAL(5, 2),
    profit_loss_ratio DECIMAL(6, 2),
    avg_holding_days DECIMAL(6, 1),

    -- 详细数据
    equity_curve JSONB,
    trade_history JSONB,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_backtest_results_symbols ON backtest_results USING GIN(symbols);
CREATE INDEX idx_backtest_results_date_range ON backtest_results(start_date, end_date);

-- ML模型表
CREATE TABLE ml_models (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID REFERENCES backtest_tasks(id),
    model_name VARCHAR(64) NOT NULL,
    model_type VARCHAR(32) NOT NULL,
    features JSONB NOT NULL,
    hyperparameters JSONB NOT NULL,

    -- 性能指标
    accuracy DECIMAL(5, 2),
    precision DECIMAL(5, 2),
    recall DECIMAL(5, 2),
    f1_score DECIMAL(5, 2),
    auc DECIMAL(5, 3),

    -- 详细数据
    feature_importance JSONB,
    confusion_matrix JSONB,

    model_file_path TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 实盘部署表
CREATE TABLE live_deployments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(128) NOT NULL,
    symbols TEXT[] NOT NULL,
    risk_config JSONB NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'running',

    -- 统计
    total_trades INTEGER DEFAULT 0,
    today_pnl DECIMAL(18, 2) DEFAULT 0,
    total_pnl DECIMAL(18, 2) DEFAULT 0,

    started_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

---

## 8. API 接口设计

### 8.1 API 端点总览

```
# 认证
POST   /api/auth/login
POST   /api/auth/logout
GET    /api/auth/me

# 任务管理
POST   /api/tasks/backtest
POST   /api/tasks/ml/train
GET    /api/tasks/{task_id}/status
GET    /api/tasks/{task_id}/results
WS     /api/tasks/ws/{task_id}

# 回测管理
GET    /api/backtest/results
GET    /api/backtest/results/{id}

# 数据管理
GET    /api/data/stats
POST   /api/data/backfill

# 实盘管理
GET    /api/live/deployments
POST   /api/live/deployments
PUT    /api/live/deployments/{id}/pause
```

---

## 9. 核心功能实现

### 9.1 任务状态管理 (前端)

```typescript
// stores/taskStore.ts
import { create } from 'zustand'

interface Task {
  id: string
  type: 'backtest' | 'ml_training'
  status: 'pending' | 'running' | 'success' | 'failed'
  progress: number
  config: any
  results?: any
  created_at: Date
}

interface TaskStore {
  tasks: Task[]
  submitTask: (type: string, config: any) => Promise<string>
  updateTaskProgress: (taskId: string, progress: number, status: string) => void
}

export const useTaskStore = create<TaskStore>((set) => ({
  tasks: [],

  submitTask: async (type, config) => {
    const response = await fetch('/api/tasks/backtest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config)
    })

    const { task_id } = await response.json()

    set(state => ({
      tasks: [...state.tasks, {
        id: task_id,
        type,
        status: 'pending',
        progress: 0,
        config,
        created_at: new Date()
      }]
    }))

    // 启动WebSocket监听
    startTaskMonitor(task_id)

    return task_id
  },

  updateTaskProgress: (taskId, progress, status) => {
    set(state => ({
      tasks: state.tasks.map(t =>
        t.id === taskId ? { ...t, progress, status } : t
      )
    }))
  }
}))
```

---

## 10. 实施计划

### Phase 1: 基础设施 (2周)
- Next.js 项目初始化
- Celery 任务队列搭建
- API 端点基础
- WebSocket 实时通信
- 数据库扩展

### Phase 2: 数据管理 (1周)
- 数据管理界面
- 批量数据回填
- 数据质量监控

### Phase 3: 回测系统 (3周)
- 研究工作台 UI
- 回测任务后端
- 回测结果页面
- 详细分析功能

### Phase 4: 机器学习 (2周)
- 特征工程配置
- 模型训练后端
- 训练结果展示
- 模型管理

### Phase 5: 实盘部署 (2周)
- 部署向导
- 实盘监控
- 部署管理

### Phase 6: 优化测试 (1周)
- 性能优化
- 响应式优化
- 测试和文档

**总计：11周**

---

## 11. 风险与挑战

### 11.1 技术风险
1. **长时间任务超时** - 设置合理超时，任务分片
2. **WebSocket连接稳定性** - 断线重连，心跳检测
3. **大数据量处理** - 分批加载，使用生成器

### 11.2 业务风险
1. **数据质量** - 数据质量检查，缺失提示
2. **回测过拟合** - 交叉验证，样本外测试
3. **实盘风险** - 严格风控，小额测试

---

## 12. 预期成果

### 12.1 系统能力
1. 完整的量化研究平台
2. 异步任务系统
3. 高质量可视化
4. 安全的实盘系统

### 12.2 性能指标
- 首屏加载 < 2秒
- 任务提交响应 < 500ms
- WebSocket延迟 < 100ms
- 支持10个并发任务

---

**文档结束**

下一步：开始实施开发，按照 Phase 1 启动项目基础设施搭建。
