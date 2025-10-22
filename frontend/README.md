# LongPort Quant - Frontend

Professional quantitative research and trading platform frontend built with Next.js 14, React, and TypeScript.

## Features

- **Professional Dark Theme** - Inspired by Bloomberg Terminal and TradingView
- **Research Workspace** - Symbol selection, backtest configuration, and task management
- **Real-time Updates** - WebSocket integration for live task progress
- **High Information Density** - Optimized layout for professional traders
- **Type-Safe** - Full TypeScript support
- **Responsive** - Tailwind CSS utility-first styling

## Tech Stack

- **Framework**: Next.js 14 (App Router)
- **Language**: TypeScript 5
- **Styling**: Tailwind CSS 3.4
- **UI Components**: Custom components + Lucide icons
- **State Management**: Zustand (planned)
- **Server State**: React Query (planned)
- **Charts**: ECharts (planned)
- **Tables**: TanStack Table (planned)

## Getting Started

### Prerequisites

- Node.js 18+ or 20+
- npm, yarn, or pnpm

### Installation

1. Install dependencies:

```bash
cd frontend
npm install
# or
pnpm install
# or
yarn install
```

2. Set environment variables:

Create a `.env.local` file:

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000
```

3. Run the development server:

```bash
npm run dev
# or
pnpm dev
# or
yarn dev
```

4. Open [http://localhost:3000](http://localhost:3000) in your browser.

## Project Structure

```
frontend/
├── app/                      # Next.js App Router
│   ├── layout.tsx           # Root layout
│   ├── page.tsx             # Dashboard home
│   ├── research/            # Research workspace
│   ├── data/                # Data management
│   ├── ml/                  # Machine learning
│   ├── live/                # Live trading
│   └── analysis/            # Performance analysis
├── components/
│   ├── ui/                  # Base UI components
│   │   ├── button.tsx
│   │   ├── card.tsx
│   │   ├── input.tsx
│   │   └── tabs.tsx
│   ├── layout/              # Layout components
│   │   ├── Sidebar.tsx
│   │   ├── Topbar.tsx
│   │   └── DashboardLayout.tsx
│   ├── metrics/             # Metric components
│   │   └── MetricCard.tsx
│   └── research/            # Research components
│       ├── SymbolSelector.tsx
│       └── BacktestWizard.tsx
├── lib/
│   └── utils.ts             # Utility functions
└── public/                  # Static assets
```

## Design System

### Color Palette

The application uses a professional dark theme with low saturation colors:

- **Background**: `#0B0E11` (primary), `#131722` (secondary)
- **Text**: `#D1D4DC` (primary), `#787B86` (secondary)
- **Long/Up**: `#089981` (dark green)
- **Short/Down**: `#F23645` (dark red)
- **Accent**: `#2962FF` (blue)

### Typography

- **UI Font**: Inter
- **Mono Font**: Roboto Mono (for numbers and code)
- **Display Font**: Inter Tight

All numbers use `font-mono` and `tabular-nums` for proper alignment.

### Spacing

Tight spacing for high information density:
- Base: 4px increments (1, 2, 3, 4, 5, 6, 8, 10)
- Smaller than standard Tailwind defaults

## Key Pages

### Dashboard (`/`)
- System overview
- Key metrics display
- Recent activity feed

### Research Workspace (`/research`)
- Symbol selector with portfolio management
- 4-step backtest configuration wizard
- Real-time task queue monitoring

### Backtest Results (`/research/results/[id]`)
- Key performance metrics
- Equity curve charts (ECharts)
- Trade history table (TanStack Table)
- Detailed analysis tabs

### Live Trading (`/live/monitor`)
- Real-time position monitoring
- Order management
- P&L tracking

## Development Guidelines

### Avoid "AI Taste"

❌ **Don't**:
- Use emojis in UI
- Use high saturation colors (green-500, red-500)
- Use large rounded corners (rounded-xl)
- Use heavy shadows (shadow-lg)
- Use casual language

✅ **Do**:
- Use professional financial terminology
- Use monospace fonts for numbers
- Use low saturation colors
- Use tight spacing (p-4 not p-8)
- Use English labels (not Chinese)

### Component Guidelines

```typescript
// ✅ Good - Professional metric card
<div className="bg-bg-tertiary border border-border-primary p-4">
  <div className="text-xs text-text-secondary uppercase">Total Return</div>
  <div className="text-2xl font-mono tabular-nums">+34.20%</div>
</div>

// ❌ Bad - "AI taste" card
<Card className="rounded-xl shadow-lg">
  <div className="text-gray-500">总收益 🚀</div>
  <div className="text-4xl text-green-600">+34.2%</div>
</Card>
```

## API Integration

The frontend connects to the FastAPI backend at `http://localhost:8000`:

```typescript
// Example API call
const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/tasks/backtest`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(backtestConfig),
})
```

## WebSocket Integration (Planned)

Real-time task progress updates:

```typescript
const ws = new WebSocket(`ws://localhost:8000/api/tasks/ws/${taskId}`)
ws.onmessage = (event) => {
  const data = JSON.parse(event.data)
  updateTaskProgress(data.progress, data.message)
}
```

## Building for Production

```bash
npm run build
npm run start
```

## References

Design inspiration from professional financial platforms:
- Bloomberg Terminal
- TradingView
- Interactive Brokers TWS
- QuantConnect

## License

Internal use only.
