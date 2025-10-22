# 长桥量化交易系统 - 专业UI设计指南

**版本**: v1.0
**日期**: 2025-01-20
**目标**: 打造专业级金融量化交易平台界面

---

## 目录

- [设计原则](#设计原则)
- [色彩系统](#色彩系统)
- [排版规范](#排版规范)
- [组件设计](#组件设计)
- [布局规范](#布局规范)
- [数据可视化](#数据可视化)
- [交互细节](#交互细节)
- [专业术语](#专业术语)

---

## 设计原则

### 核心原则

1. **数据优先** - 界面服务于数据展示和分析，避免视觉噪音
2. **信息密度** - 专业用户需要高信息密度，合理利用屏幕空间
3. **视觉层次** - 清晰的信息层级，关键数据一目了然
4. **一致性** - 统一的交互模式和视觉语言
5. **性能感知** - 即时反馈，减少等待焦虑

### 避免的"AI味"特征

❌ **不要做**:
- 过度使用emoji和装饰图标
- 使用鲜艳、饱和度高的颜色
- 大量留白和"呼吸感"设计
- 过于圆润的圆角和阴影
- 友好、口语化的文案
- 过度动画效果

✓ **应该做**:
- 使用专业的monospace字体显示数字
- 采用低饱和度、高对比度配色
- 紧凑的信息布局，充分利用空间
- 精确的数据对齐和网格系统
- 专业术语和金融行业标准表达
- 快速、流畅的交互反馈

---

## 色彩系统

### 主色调方案

参考 Bloomberg Terminal、TradingView 等专业平台的配色：

```css
/* 主题色 - 深色模式为主 */
--background-primary: #0B0E11;      /* 主背景 - 深灰黑 */
--background-secondary: #131722;    /* 次级背景 */
--background-tertiary: #1C2128;     /* 卡片背景 */
--background-elevated: #242931;     /* 悬浮层背景 */

/* 边框和分隔 */
--border-primary: #2A2E39;          /* 主边框 */
--border-secondary: #363A45;        /* 次级边框 */
--border-hover: #434651;            /* 悬停边框 */

/* 文字 */
--text-primary: #D1D4DC;            /* 主文字 - 高对比 */
--text-secondary: #787B86;          /* 次级文字 */
--text-tertiary: #53565F;           /* 辅助文字 */
--text-disabled: #363A45;           /* 禁用文字 */

/* 数据色彩 */
--color-long: #089981;              /* 做多/上涨 - 偏暗绿 */
--color-short: #F23645;             /* 做空/下跌 - 偏暗红 */
--color-neutral: #787B86;           /* 中性 */

/* 功能色 */
--color-info: #2962FF;              /* 信息 */
--color-warning: #FF6D00;           /* 警告 */
--color-danger: #F23645;            /* 危险 */
--color-success: #089981;           /* 成功 */

/* 强调色 */
--accent-primary: #2962FF;          /* 主要操作 */
--accent-secondary: #5E81F4;        /* 次要操作 */
```

### 浅色模式（可选）

```css
/* 浅色主题 - 参考 TradingView Light */
--background-primary: #FFFFFF;
--background-secondary: #F7F8FA;
--background-tertiary: #FAFBFC;
--border-primary: #E0E3EB;
--text-primary: #131722;
--text-secondary: #434651;
--color-long: #00897B;
--color-short: #D32F2F;
```

### 语义化颜色使用

```typescript
// 收益率颜色
function getReturnColor(value: number) {
  if (value > 0) return 'var(--color-long)'
  if (value < 0) return 'var(--color-short)'
  return 'var(--color-neutral)'
}

// 风险等级颜色
function getRiskColor(level: 'low' | 'medium' | 'high') {
  switch(level) {
    case 'low': return 'var(--color-success)'
    case 'medium': return 'var(--color-warning)'
    case 'high': return 'var(--color-danger)'
  }
}
```

---

## 排版规范

### 字体系统

```css
/* 字体家族 */
--font-ui: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
--font-mono: 'Roboto Mono', 'SF Mono', 'Consolas', monospace;
--font-display: 'Inter Tight', -apple-system, sans-serif;

/* 字体大小 - 使用固定像素值 */
--text-xs: 11px;      /* 辅助信息 */
--text-sm: 12px;      /* 次要文字 */
--text-base: 13px;    /* 主要文字 - 注意比常规小 */
--text-lg: 14px;      /* 标题文字 */
--text-xl: 16px;      /* 大标题 */
--text-2xl: 20px;     /* 特大标题 */

/* 字重 */
--font-normal: 400;
--font-medium: 500;
--font-semibold: 600;
--font-bold: 700;

/* 行高 - 紧凑为主 */
--leading-tight: 1.25;
--leading-normal: 1.4;
--leading-relaxed: 1.6;
```

### 数字显示规范

```typescript
// 所有数字使用 monospace 字体
<span className="font-mono tabular-nums">1,234,567.89</span>

// 收益率显示
function formatReturn(value: number): string {
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

// 价格显示（根据资产类型）
function formatPrice(value: number, precision: number = 2): string {
  return value.toLocaleString('en-US', {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision
  })
}

// 大数字缩写
function formatLargeNumber(value: number): string {
  if (value >= 1e9) return `${(value / 1e9).toFixed(2)}B`
  if (value >= 1e6) return `${(value / 1e6).toFixed(2)}M`
  if (value >= 1e3) return `${(value / 1e3).toFixed(2)}K`
  return value.toFixed(2)
}
```

### 间距系统

```css
/* 紧凑的间距系统 - 参考专业交易平台 */
--space-0: 0px;
--space-1: 4px;
--space-2: 8px;
--space-3: 12px;
--space-4: 16px;
--space-5: 20px;
--space-6: 24px;
--space-8: 32px;
--space-10: 40px;
```

---

## 组件设计

### 1. 指标卡片（Metric Card）

专业版本 - 去除装饰，聚焦数据：

```typescript
// components/metrics/MetricCard.tsx
interface MetricCardProps {
  label: string
  value: string | number
  change?: number
  changeLabel?: string
  precision?: number
  format?: 'number' | 'percent' | 'currency'
}

export function MetricCard({
  label,
  value,
  change,
  changeLabel = 'vs Previous',
  precision = 2,
  format = 'number'
}: MetricCardProps) {
  const formattedValue = formatValue(value, format, precision)
  const changeColor = change > 0 ? 'text-long' : change < 0 ? 'text-short' : 'text-neutral'

  return (
    <div className="bg-tertiary border border-primary p-4">
      {/* 标签 */}
      <div className="text-xs text-secondary uppercase tracking-wider mb-2">
        {label}
      </div>

      {/* 数值 */}
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-mono font-semibold text-primary tabular-nums">
          {formattedValue}
        </span>

        {/* 变化 */}
        {change !== undefined && (
          <div className="flex items-center gap-1 text-sm">
            <span className={`font-mono tabular-nums ${changeColor}`}>
              {change > 0 ? '+' : ''}{change.toFixed(2)}%
            </span>
          </div>
        )}
      </div>

      {/* 次级信息 */}
      {change !== undefined && (
        <div className="mt-1 text-xs text-tertiary">
          {changeLabel}
        </div>
      )}
    </div>
  )
}
```

### 2. 数据表格（Data Table）

高信息密度的专业表格：

```typescript
// components/tables/TradeTable.tsx
import { flexRender, getCoreRowModel, useReactTable } from '@tanstack/react-table'

interface Trade {
  timestamp: string
  symbol: string
  side: 'BUY' | 'SELL'
  quantity: number
  price: number
  value: number
  pnl?: number
  pnlPercent?: number
}

export function TradeTable({ data }: { data: Trade[] }) {
  const columns = [
    {
      accessorKey: 'timestamp',
      header: 'Time',
      cell: ({ getValue }) => (
        <span className="font-mono text-xs text-secondary">
          {formatTime(getValue() as string)}
        </span>
      ),
    },
    {
      accessorKey: 'symbol',
      header: 'Symbol',
      cell: ({ getValue }) => (
        <span className="font-mono text-sm font-medium">
          {getValue() as string}
        </span>
      ),
    },
    {
      accessorKey: 'side',
      header: 'Side',
      cell: ({ getValue }) => {
        const side = getValue() as string
        return (
          <span className={`
            text-xs font-medium px-2 py-0.5 rounded
            ${side === 'BUY' ? 'bg-long/10 text-long' : 'bg-short/10 text-short'}
          `}>
            {side}
          </span>
        )
      },
    },
    {
      accessorKey: 'quantity',
      header: () => <div className="text-right">Qty</div>,
      cell: ({ getValue }) => (
        <div className="text-right font-mono tabular-nums">
          {(getValue() as number).toLocaleString()}
        </div>
      ),
    },
    {
      accessorKey: 'price',
      header: () => <div className="text-right">Price</div>,
      cell: ({ getValue }) => (
        <div className="text-right font-mono tabular-nums">
          {(getValue() as number).toFixed(2)}
        </div>
      ),
    },
    {
      accessorKey: 'value',
      header: () => <div className="text-right">Value</div>,
      cell: ({ getValue }) => (
        <div className="text-right font-mono tabular-nums">
          {formatCurrency(getValue() as number)}
        </div>
      ),
    },
    {
      accessorKey: 'pnl',
      header: () => <div className="text-right">P&L</div>,
      cell: ({ row }) => {
        const pnl = row.original.pnl
        const pnlPercent = row.original.pnlPercent
        if (pnl === undefined) return <div className="text-right text-tertiary">-</div>

        const color = pnl > 0 ? 'text-long' : pnl < 0 ? 'text-short' : 'text-neutral'
        return (
          <div className="text-right">
            <div className={`font-mono tabular-nums ${color}`}>
              {pnl > 0 ? '+' : ''}{formatCurrency(pnl)}
            </div>
            {pnlPercent !== undefined && (
              <div className={`text-xs font-mono tabular-nums ${color}`}>
                {pnlPercent > 0 ? '+' : ''}{pnlPercent.toFixed(2)}%
              </div>
            )}
          </div>
        )
      },
    },
  ]

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
  })

  return (
    <div className="border border-primary rounded overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-secondary border-b border-primary">
          {table.getHeaderGroups().map(headerGroup => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map(header => (
                <th
                  key={header.id}
                  className="px-3 py-2 text-left text-xs font-medium text-secondary uppercase tracking-wider"
                >
                  {flexRender(header.column.columnDef.header, header.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody className="divide-y divide-primary">
          {table.getRowModel().rows.map(row => (
            <tr
              key={row.id}
              className="hover:bg-secondary/50 transition-colors"
            >
              {row.getVisibleCells().map(cell => (
                <td key={cell.id} className="px-3 py-2">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
```

### 3. 侧边栏导航

去除emoji，使用简洁的文本导航：

```typescript
// components/layout/Sidebar.tsx
const navigation = [
  { name: 'Research', href: '/research', section: 'workspace' },
  { name: 'Backtest Results', href: '/research/results', section: 'workspace' },
  { name: 'Strategy Library', href: '/research/library', section: 'workspace' },

  { name: 'Data Manager', href: '/data', section: 'data' },
  { name: 'Data Quality', href: '/data/quality', section: 'data' },

  { name: 'Model Training', href: '/ml/training', section: 'ml' },
  { name: 'Model Registry', href: '/ml/models', section: 'ml' },

  { name: 'Live Trading', href: '/live/monitor', section: 'live' },
  { name: 'Positions', href: '/live/positions', section: 'live' },
  { name: 'Order Management', href: '/live/orders', section: 'live' },

  { name: 'Performance', href: '/analysis/performance', section: 'analysis' },
  { name: 'Risk Analytics', href: '/analysis/risk', section: 'analysis' },

  { name: 'Settings', href: '/settings', section: 'settings' },
]

const sections = {
  workspace: 'Research Workspace',
  data: 'Data Management',
  ml: 'Machine Learning',
  live: 'Live Trading',
  analysis: 'Analytics',
  settings: 'Configuration',
}

export function Sidebar() {
  return (
    <aside className="w-56 bg-secondary border-r border-primary flex flex-col">
      {/* Logo */}
      <div className="h-14 flex items-center px-4 border-b border-primary">
        <span className="text-base font-semibold text-primary">LongPort Quant</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4 overflow-y-auto">
        {Object.entries(sections).map(([key, title]) => {
          const items = navigation.filter(item => item.section === key)
          if (items.length === 0) return null

          return (
            <div key={key} className="mb-6">
              {/* Section Header */}
              <div className="px-4 mb-2">
                <h3 className="text-xs font-medium text-tertiary uppercase tracking-wider">
                  {title}
                </h3>
              </div>

              {/* Section Items */}
              <div className="space-y-0.5">
                {items.map(item => (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cn(
                      "block px-4 py-1.5 text-sm transition-colors",
                      "hover:bg-tertiary hover:text-primary",
                      pathname === item.href
                        ? "bg-tertiary text-primary font-medium"
                        : "text-secondary"
                    )}
                  >
                    {item.name}
                  </Link>
                ))}
              </div>
            </div>
          )
        })}
      </nav>

      {/* System Status */}
      <div className="border-t border-primary p-4">
        <div className="flex items-center justify-between text-xs">
          <span className="text-tertiary">System Status</span>
          <div className="flex items-center gap-1.5">
            <div className="w-1.5 h-1.5 rounded-full bg-success" />
            <span className="text-secondary font-mono">Operational</span>
          </div>
        </div>
        <div className="mt-2 flex items-center justify-between text-xs">
          <span className="text-tertiary">Market Session</span>
          <span className="text-secondary font-mono">Pre-Market</span>
        </div>
      </div>
    </aside>
  )
}
```

### 4. 顶部状态栏

显示实时市场信息和系统状态：

```typescript
// components/layout/Topbar.tsx
export function Topbar() {
  const { data: marketData } = useQuery({
    queryKey: ['market-status'],
    queryFn: fetchMarketStatus,
    refetchInterval: 5000, // 5秒刷新
  })

  return (
    <header className="h-10 bg-secondary border-b border-primary flex items-center justify-between px-4">
      {/* Left: Market Indices */}
      <div className="flex items-center gap-6">
        <MarketIndex name="HSI" value={18234.56} change={+1.24} />
        <MarketIndex name="SPX" value={4789.32} change={-0.38} />
        <MarketIndex name="NASDAQ" value={14832.67} change={+0.56} />
      </div>

      {/* Right: User & Controls */}
      <div className="flex items-center gap-4">
        {/* Market Session */}
        <div className="flex items-center gap-2 text-xs">
          <span className="text-tertiary">HK Market</span>
          <span className={cn(
            "font-mono font-medium px-2 py-0.5 rounded",
            marketData?.hkStatus === 'OPEN'
              ? "bg-success/10 text-success"
              : "bg-neutral/10 text-neutral"
          )}>
            {marketData?.hkStatus || 'CLOSED'}
          </span>
        </div>

        <div className="w-px h-4 bg-border-primary" />

        {/* User */}
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-full bg-accent-primary/20 flex items-center justify-center">
            <span className="text-xs font-medium text-accent-primary">JD</span>
          </div>
          <span className="text-sm text-secondary">john.doe</span>
        </div>
      </div>
    </header>
  )
}

function MarketIndex({ name, value, change }: {
  name: string
  value: number
  change: number
}) {
  const changeColor = change >= 0 ? 'text-long' : 'text-short'

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-tertiary font-medium">{name}</span>
      <span className="text-sm font-mono tabular-nums text-primary">
        {value.toFixed(2)}
      </span>
      <span className={`text-xs font-mono tabular-nums ${changeColor}`}>
        {change >= 0 ? '+' : ''}{change.toFixed(2)}%
      </span>
    </div>
  )
}
```

---

## 布局规范

### 研究工作台布局

```typescript
// app/(dashboard)/research/workspace/page.tsx
export default function ResearchWorkspace() {
  return (
    <div className="h-full flex">
      {/* Left Panel: Symbol Selector - 320px fixed */}
      <div className="w-80 border-r border-primary flex flex-col">
        <SymbolSelector />
      </div>

      {/* Center: Configuration - flex-1 */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <div className="h-12 border-b border-primary flex items-center justify-between px-6">
          <h1 className="text-lg font-semibold text-primary">Backtest Configuration</h1>
          <div className="flex items-center gap-2 text-xs text-tertiary">
            <span>Last saved: 2 minutes ago</span>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto">
          <BacktestWizard />
        </div>
      </div>

      {/* Right Panel: Task Queue - 360px fixed */}
      <div className="w-90 border-l border-primary">
        <TaskQueue />
      </div>
    </div>
  )
}
```

### 回测结果页面布局

```typescript
// app/(dashboard)/research/results/[id]/page.tsx
export default async function BacktestResultPage({ params }: { params: { id: string } }) {
  const result = await getBacktestResult(params.id)

  return (
    <div className="h-full flex flex-col">
      {/* Header Bar */}
      <div className="h-14 border-b border-primary flex items-center justify-between px-6">
        <div className="flex items-center gap-4">
          <Button variant="ghost" size="sm">
            <ArrowLeftIcon className="w-4 h-4" />
          </Button>
          <div>
            <h1 className="text-base font-semibold text-primary">{result.name}</h1>
            <div className="text-xs text-tertiary font-mono">
              {result.symbols.join(', ')} · {result.dateRange}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs text-tertiary">Status:</span>
          <span className="text-xs font-medium text-success">Completed</span>
          <span className="text-xs text-tertiary font-mono">{result.duration}</span>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6 space-y-6">
        {/* Key Metrics Grid */}
        <div className="grid grid-cols-6 gap-4">
          <MetricCard label="Total Return" value={result.totalReturn} format="percent" change={12.5} />
          <MetricCard label="Annual Return" value={result.annualReturn} format="percent" />
          <MetricCard label="Sharpe Ratio" value={result.sharpeRatio} precision={3} />
          <MetricCard label="Max Drawdown" value={result.maxDrawdown} format="percent" />
          <MetricCard label="Win Rate" value={result.winRate} format="percent" />
          <MetricCard label="Total Trades" value={result.totalTrades} />
        </div>

        {/* Equity Curve Chart */}
        <div className="bg-tertiary border border-primary p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-primary uppercase tracking-wider">
              Equity Curve
            </h2>
            <ChartControls />
          </div>
          <EquityCurveChart data={result.equityCurve} height={400} />
        </div>

        {/* Detailed Analysis Tabs */}
        <Tabs defaultValue="trades">
          <TabsList className="border-b border-primary">
            <TabsTrigger value="trades">Trade History</TabsTrigger>
            <TabsTrigger value="positions">Position Analysis</TabsTrigger>
            <TabsTrigger value="drawdown">Drawdown Analysis</TabsTrigger>
            <TabsTrigger value="monthly">Monthly Returns</TabsTrigger>
            <TabsTrigger value="benchmark">Benchmark Comparison</TabsTrigger>
          </TabsList>

          <TabsContent value="trades" className="mt-4">
            <TradeTable data={result.trades} />
          </TabsContent>
          {/* ... other tabs */}
        </Tabs>
      </div>

      {/* Action Bar */}
      <div className="h-14 border-t border-primary flex items-center justify-end gap-2 px-6">
        <Button variant="outline" size="sm">Export Report</Button>
        <Button variant="outline" size="sm">Clone & Edit</Button>
        <Button variant="primary" size="sm">Deploy to Live</Button>
      </div>
    </div>
  )
}
```

---

## 数据可视化

### ECharts 配置 - 专业深色主题

```typescript
// lib/echarts-theme.ts
export const darkTheme = {
  backgroundColor: 'transparent',
  textStyle: {
    color: '#D1D4DC',
    fontFamily: 'Inter, sans-serif',
    fontSize: 12,
  },
  title: {
    textStyle: {
      color: '#D1D4DC',
      fontSize: 14,
      fontWeight: 600,
    },
  },
  grid: {
    left: 60,
    right: 60,
    top: 40,
    bottom: 40,
    borderColor: '#2A2E39',
  },
  xAxis: {
    axisLine: { lineStyle: { color: '#2A2E39' } },
    axisLabel: {
      color: '#787B86',
      fontSize: 11,
      fontFamily: 'Roboto Mono, monospace',
    },
    splitLine: {
      lineStyle: { color: '#2A2E39', type: 'dashed' },
    },
  },
  yAxis: {
    axisLine: { lineStyle: { color: '#2A2E39' } },
    axisLabel: {
      color: '#787B86',
      fontSize: 11,
      fontFamily: 'Roboto Mono, monospace',
      formatter: (value: number) => {
        if (Math.abs(value) >= 1000) {
          return (value / 1000).toFixed(1) + 'K'
        }
        return value.toFixed(0)
      },
    },
    splitLine: {
      lineStyle: { color: '#2A2E39', type: 'dashed' },
    },
  },
  tooltip: {
    backgroundColor: '#1C2128',
    borderColor: '#2A2E39',
    borderWidth: 1,
    textStyle: {
      color: '#D1D4DC',
      fontSize: 12,
    },
    axisPointer: {
      lineStyle: { color: '#434651' },
      crossStyle: { color: '#434651' },
    },
  },
  legend: {
    textStyle: { color: '#787B86', fontSize: 11 },
    inactiveColor: '#53565F',
  },
}

// 收益曲线专用配置
export function getEquityCurveOption(data: EquityCurveData) {
  return {
    ...darkTheme,
    tooltip: {
      ...darkTheme.tooltip,
      trigger: 'axis',
      formatter: (params: any) => {
        const date = params[0].axisValue
        let content = `<div class="font-mono text-xs">${date}</div>`

        params.forEach((param: any) => {
          const value = param.value
          const color = param.seriesName.includes('Strategy')
            ? (value >= 0 ? '#089981' : '#F23645')
            : '#787B86'

          content += `
            <div class="flex items-center justify-between gap-4 mt-1">
              <span>${param.marker} ${param.seriesName}</span>
              <span class="font-mono" style="color: ${color}">
                ${value >= 0 ? '+' : ''}${value.toFixed(2)}%
              </span>
            </div>
          `
        })

        return content
      },
    },
    xAxis: {
      ...darkTheme.xAxis,
      type: 'category',
      data: data.dates,
      boundaryGap: false,
    },
    yAxis: [
      {
        ...darkTheme.yAxis,
        type: 'value',
        name: 'Return (%)',
        position: 'left',
        nameTextStyle: {
          color: '#787B86',
          fontSize: 11,
        },
      },
      {
        ...darkTheme.yAxis,
        type: 'value',
        name: 'Drawdown (%)',
        position: 'right',
        inverse: true,
        nameTextStyle: {
          color: '#787B86',
          fontSize: 11,
        },
      },
    ],
    series: [
      {
        name: 'Strategy Return',
        type: 'line',
        data: data.strategyReturns,
        lineStyle: { color: '#089981', width: 2 },
        itemStyle: { color: '#089981' },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(8, 153, 129, 0.15)' },
              { offset: 1, color: 'rgba(8, 153, 129, 0.02)' },
            ],
          },
        },
        smooth: false, // 不平滑，显示真实波动
      },
      {
        name: 'Benchmark Return',
        type: 'line',
        data: data.benchmarkReturns,
        lineStyle: { color: '#787B86', width: 1, type: 'dashed' },
        itemStyle: { color: '#787B86' },
      },
      {
        name: 'Drawdown',
        type: 'line',
        yAxisIndex: 1,
        data: data.drawdowns.map(d => Math.abs(d)),
        lineStyle: { color: '#F23645', width: 1 },
        itemStyle: { color: '#F23645' },
        areaStyle: {
          color: 'rgba(242, 54, 69, 0.08)',
        },
      },
    ],
  }
}
```

---

## 交互细节

### 加载状态

不要使用spinner，使用骨架屏或进度指示：

```typescript
// components/ui/TableSkeleton.tsx
export function TableSkeleton({ rows = 10 }: { rows?: number }) {
  return (
    <div className="border border-primary rounded overflow-hidden">
      <div className="bg-secondary h-9 border-b border-primary" />
      <div className="divide-y divide-primary">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="h-11 px-3 flex items-center gap-4">
            <div className="h-3 w-20 bg-tertiary rounded animate-pulse" />
            <div className="h-3 w-16 bg-tertiary rounded animate-pulse" />
            <div className="h-3 w-24 bg-tertiary rounded animate-pulse" />
            <div className="h-3 flex-1 bg-tertiary rounded animate-pulse" />
          </div>
        ))}
      </div>
    </div>
  )
}
```

### 任务进度

使用精确的进度条和状态：

```typescript
// components/tasks/TaskProgress.tsx
interface TaskProgressProps {
  taskId: string
  progress: number
  status: 'pending' | 'running' | 'success' | 'failed'
  message?: string
  startTime: Date
}

export function TaskProgress({
  taskId,
  progress,
  status,
  message,
  startTime
}: TaskProgressProps) {
  const elapsed = Math.floor((Date.now() - startTime.getTime()) / 1000)

  return (
    <div className="border border-primary rounded p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <StatusIndicator status={status} />
          <span className="text-sm font-mono text-tertiary">{taskId}</span>
        </div>
        <span className="text-xs text-tertiary font-mono">
          {formatDuration(elapsed)}
        </span>
      </div>

      {/* Progress Bar */}
      <div className="h-1 bg-tertiary rounded-full overflow-hidden mb-2">
        <div
          className={cn(
            "h-full transition-all duration-300",
            status === 'running' && "bg-accent-primary",
            status === 'success' && "bg-success",
            status === 'failed' && "bg-danger",
          )}
          style={{ width: `${progress}%` }}
        />
      </div>

      {/* Message */}
      <div className="flex items-center justify-between">
        <span className="text-xs text-secondary">{message || 'Processing...'}</span>
        <span className="text-xs text-tertiary font-mono tabular-nums">
          {progress.toFixed(1)}%
        </span>
      </div>
    </div>
  )
}

function StatusIndicator({ status }: { status: string }) {
  const colors = {
    pending: 'bg-neutral',
    running: 'bg-accent-primary animate-pulse',
    success: 'bg-success',
    failed: 'bg-danger',
  }

  return <div className={`w-2 h-2 rounded-full ${colors[status]}`} />
}
```

---

## 专业术语

### 界面文案规范

❌ **不专业**:
- "总收益" → ✓ "Total Return"
- "赚钱率" → ✓ "Win Rate" 或 "Win Ratio"
- "最大跌幅" → ✓ "Maximum Drawdown"
- "好的策略" → ✓ "High-Performance Strategy"
- "跑回测" → ✓ "Execute Backtest" 或 "Run Backtest"

✓ **专业表达**:
- Performance Metrics
- Risk-Adjusted Return
- Sharpe Ratio / Sortino Ratio
- Alpha / Beta
- Information Ratio
- Position Sizing
- Portfolio Rebalancing
- Execution Slippage
- Market Impact
- Factor Exposure
- Benchmark Attribution
- Out-of-Sample Testing
- Walk-Forward Analysis
- Monte Carlo Simulation

### 缩写规范

使用行业标准缩写：

```typescript
const ABBREVIATIONS = {
  'P&L': 'Profit & Loss',
  'YTD': 'Year-to-Date',
  'MTD': 'Month-to-Date',
  'QTD': 'Quarter-to-Date',
  'AUM': 'Assets Under Management',
  'NAV': 'Net Asset Value',
  'VWAP': 'Volume-Weighted Average Price',
  'TWAP': 'Time-Weighted Average Price',
  'OMS': 'Order Management System',
  'EMS': 'Execution Management System',
  'PMS': 'Portfolio Management System',
}
```

---

## Tailwind CSS 配置

```javascript
// tailwind.config.js
module.exports = {
  darkMode: 'class',
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Background
        'bg-primary': 'var(--background-primary)',
        'bg-secondary': 'var(--background-secondary)',
        'bg-tertiary': 'var(--background-tertiary)',
        'bg-elevated': 'var(--background-elevated)',

        // Borders
        'border-primary': 'var(--border-primary)',
        'border-secondary': 'var(--border-secondary)',
        'border-hover': 'var(--border-hover)',

        // Text
        'text-primary': 'var(--text-primary)',
        'text-secondary': 'var(--text-secondary)',
        'text-tertiary': 'var(--text-tertiary)',
        'text-disabled': 'var(--text-disabled)',

        // Data colors
        'long': 'var(--color-long)',
        'short': 'var(--color-short)',
        'neutral': 'var(--color-neutral)',

        // Functional
        'info': 'var(--color-info)',
        'warning': 'var(--color-warning)',
        'danger': 'var(--color-danger)',
        'success': 'var(--color-success)',

        // Accent
        'accent-primary': 'var(--accent-primary)',
        'accent-secondary': 'var(--accent-secondary)',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui'],
        mono: ['Roboto Mono', 'SF Mono', 'Consolas', 'monospace'],
        display: ['Inter Tight', 'ui-sans-serif', 'system-ui'],
      },
      fontSize: {
        xs: ['11px', '16px'],
        sm: ['12px', '18px'],
        base: ['13px', '20px'],
        lg: ['14px', '22px'],
        xl: ['16px', '24px'],
        '2xl': ['20px', '28px'],
      },
      spacing: {
        '0': '0px',
        '1': '4px',
        '2': '8px',
        '3': '12px',
        '4': '16px',
        '5': '20px',
        '6': '24px',
        '8': '32px',
        '10': '40px',
      },
      borderRadius: {
        'none': '0',
        'sm': '2px',
        DEFAULT: '4px',
        'md': '6px',
        'lg': '8px',
      },
    },
  },
  plugins: [],
}
```

---

## 总结

### 核心改进点

1. **去除所有emoji** - 使用文本和专业术语
2. **深色配色为主** - 参考专业交易平台
3. **高信息密度** - 紧凑布局，充分利用空间
4. **Monospace数字** - 所有数值使用等宽字体
5. **专业术语** - 使用行业标准表达
6. **精确对齐** - 数字右对齐，严格网格
7. **低饱和度色彩** - 避免鲜艳颜色
8. **最小化装饰** - 去除不必要的图标和动画

### 参考资源

- **Bloomberg Terminal** - 金融数据终端标准
- **TradingView** - 专业图表和配色
- **Interactive Brokers TWS** - 交易系统UI
- **QuantConnect** - 量化平台界面设计

---

**下一步**: 基于这份设计指南开始实施前端开发
