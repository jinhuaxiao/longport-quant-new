# 前端快速开始指南

本文档帮助你快速启动长桥量化交易前端项目。

---

## 📋 前置要求

- **Node.js** 18+ 或 20+
- **npm**, **yarn**, 或 **pnpm** (推荐 pnpm)
- **Git**

---

## 🚀 快速启动

### 1. 安装依赖

```bash
cd frontend
pnpm install
# 或
npm install
# 或
yarn install
```

### 2. 配置环境变量

创建 `.env.local` 文件：

```bash
cp .env.local.example .env.local
```

编辑 `.env.local`：

```bash
# 后端 API 地址
NEXT_PUBLIC_API_URL=http://localhost:8000

# WebSocket 地址
NEXT_PUBLIC_WS_URL=ws://localhost:8000
```

### 3. 启动开发服务器

```bash
pnpm dev
# 或
npm run dev
# 或
yarn dev
```

### 4. 访问应用

打开浏览器访问 [http://localhost:3000](http://localhost:3000)

---

## 📁 项目结构说明

```
frontend/
├── app/                          # Next.js App Router
│   ├── layout.tsx               # 根布局（字体配置）
│   ├── globals.css              # 全局样式（专业主题）
│   ├── page.tsx                 # 主页（Dashboard）
│   └── research/
│       └── page.tsx             # 研究工作台
│
├── components/
│   ├── ui/                      # 基础 UI 组件
│   │   ├── button.tsx          # 按钮
│   │   ├── card.tsx            # 卡片
│   │   ├── input.tsx           # 输入框
│   │   └── tabs.tsx            # 标签页
│   │
│   ├── layout/                  # 布局组件
│   │   ├── Sidebar.tsx         # 侧边栏导航
│   │   ├── Topbar.tsx          # 顶部状态栏
│   │   └── DashboardLayout.tsx # 主布局容器
│   │
│   ├── metrics/                 # 指标组件
│   │   └── MetricCard.tsx      # 指标卡片
│   │
│   └── research/                # 研究工作台组件
│       ├── SymbolSelector.tsx  # 标的选择器
│       └── BacktestWizard.tsx  # 回测配置向导
│
├── lib/
│   └── utils.ts                 # 工具函数
│
├── package.json                 # 依赖配置
├── tsconfig.json                # TypeScript 配置
├── tailwind.config.js           # Tailwind 配置（专业主题）
└── next.config.js               # Next.js 配置
```

---

## 🎨 设计系统

### 色彩方案

已配置专业的深色主题（参考 Bloomberg/TradingView）：

| 用途 | CSS 变量 | 颜色值 |
|------|---------|--------|
| 主背景 | `--background-primary` | `#0B0E11` |
| 次背景 | `--background-secondary` | `#131722` |
| 卡片背景 | `--background-tertiary` | `#1C2128` |
| 主文字 | `--text-primary` | `#D1D4DC` |
| 次文字 | `--text-secondary` | `#787B86` |
| 做多/上涨 | `--color-long` | `#089981` |
| 做空/下跌 | `--color-short` | `#F23645` |
| 强调色 | `--accent-primary` | `#2962FF` |

### 使用方式

```tsx
// 使用 Tailwind class
<div className="bg-bg-tertiary text-text-primary border border-border-primary">
  <span className="text-long font-mono tabular-nums">+12.5%</span>
</div>

// 或使用 CSS 变量
<div style={{ color: 'var(--color-long)' }}>
  Profit
</div>
```

### 字体

- **UI 文字**: Inter
- **数字/代码**: Roboto Mono
- **标题**: Inter Tight

所有数字必须使用 `font-mono` + `tabular-nums`：

```tsx
<span className="font-mono tabular-nums">1,234.56</span>
```

---

## 📄 已实现的页面

### 1. Dashboard 主页 (`/`)

- 系统概览
- 6个关键指标卡片
- 最近活动流

### 2. 研究工作台 (`/research`)

**三栏布局**:
- **左侧 (320px)**: 标的选择器
  - 搜索功能
  - 已选列表
  - 投资组合管理

- **中间 (flex)**: 回测配置向导
  - Step 1: 数据准备（日期、频率）
  - Step 2: 策略选择（MA、RSI、MACD等）
  - Step 3: 参数配置
  - Step 4: 风控设置

- **右侧 (360px)**: 任务队列
  - 运行中的任务
  - 已完成的任务
  - 失败的任务

---

## 🔧 常用命令

```bash
# 开发环境
pnpm dev

# 类型检查
pnpm tsc --noEmit

# 构建生产版本
pnpm build

# 启动生产服务器
pnpm start

# 代码检查
pnpm lint
```

---

## 🎯 下一步开发

### 待实现功能

1. **回测结果页面** (`/research/results/[id]`)
   - 指标网格
   - ECharts 收益曲线
   - TanStack Table 交易记录
   - 详细分析 Tabs

2. **API 集成**
   - React Query 配置
   - API 客户端封装
   - 错误处理
   - 加载状态

3. **WebSocket 集成**
   - 实时任务进度
   - 市场数据推送
   - 断线重连

4. **状态管理**
   - Zustand store 配置
   - 任务状态管理
   - 用户偏好设置

5. **数据可视化**
   - ECharts 图表组件
   - 收益曲线
   - 回撤曲线
   - 月度收益

6. **实盘监控页面** (`/live/monitor`)
   - 持仓监控
   - 订单管理
   - P&L 追踪

---

## 📚 重要文档

- [专业UI设计指南](../docs/architecture/PROFESSIONAL_UI_DESIGN_GUIDE.md)
- [UI改进对比](../docs/architecture/UI_IMPROVEMENT_COMPARISON.md)
- [前端技术方案](../docs/architecture/QUANT_FRONTEND_TECHNICAL_PROPOSAL.md)

---

## 💡 开发提示

### 创建新组件

```bash
# 基础 UI 组件
touch components/ui/select.tsx

# 业务组件
touch components/research/StrategyEditor.tsx
```

### 添加新页面

```bash
# 创建新路由
mkdir -p app/live/monitor
touch app/live/monitor/page.tsx
```

### 样式调试

使用 Chrome DevTools 查看 CSS 变量：

```javascript
// 在控制台运行
getComputedStyle(document.documentElement).getPropertyValue('--text-primary')
```

---

## ⚠️ 常见问题

### Q: 为什么数字显示不对齐？

A: 确保使用了 `font-mono` 和 `tabular-nums`：

```tsx
// ❌ 错误
<span>1234.56</span>

// ✅ 正确
<span className="font-mono tabular-nums">1,234.56</span>
```

### Q: 如何调整间距？

A: 使用紧凑的间距系统（4px 倍数）：

```tsx
// ❌ 太大
<div className="p-8 gap-8">

// ✅ 合适
<div className="p-4 gap-4">
```

### Q: 颜色太鲜艳怎么办？

A: 不要使用 Tailwind 默认颜色，使用主题颜色：

```tsx
// ❌ 太鲜艳
<span className="text-green-500">+10%</span>

// ✅ 专业
<span className="text-long font-mono">+10.00%</span>
```

---

## 🤝 贡献指南

### 代码风格

- **无 emoji**: 不在 UI 中使用 emoji
- **英文标签**: 使用专业金融术语
- **Monospace 数字**: 所有数字使用等宽字体
- **紧凑布局**: 使用 `p-4` 而非 `p-8`
- **低饱和度**: 使用主题颜色而非 Tailwind 默认色

### 提交规范

```bash
git commit -m "feat: 添加回测结果页面"
git commit -m "fix: 修复任务进度显示问题"
git commit -m "docs: 更新 API 文档"
```

---

## 📞 获取帮助

- 查看 [设计指南](../docs/architecture/PROFESSIONAL_UI_DESIGN_GUIDE.md)
- 参考 [对比文档](../docs/architecture/UI_IMPROVEMENT_COMPARISON.md)
- 查看已实现组件的代码

---

**开始构建专业的量化交易平台！** 🚀
