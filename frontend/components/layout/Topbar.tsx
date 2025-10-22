'use client'

import { cn } from '@/lib/utils'

interface MarketIndexProps {
  name: string
  value: number
  change: number
}

function MarketIndex({ name, value, change }: MarketIndexProps) {
  const changeColor = change >= 0 ? 'text-long' : 'text-short'

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-text-tertiary font-medium">{name}</span>
      <span className="text-sm font-mono tabular-nums text-text-primary">
        {value.toFixed(2)}
      </span>
      <span className={cn('text-xs font-mono tabular-nums', changeColor)}>
        {change >= 0 ? '+' : ''}{change.toFixed(2)}%
      </span>
    </div>
  )
}

export function Topbar() {
  // In production, fetch from API
  const marketData = {
    hkStatus: 'CLOSED',
    indices: [
      { name: 'HSI', value: 18234.56, change: 1.24 },
      { name: 'SPX', value: 4789.32, change: -0.38 },
      { name: 'NASDAQ', value: 14832.67, change: 0.56 },
    ],
  }

  return (
    <header className="h-10 bg-bg-secondary border-b border-border-primary flex items-center justify-between px-4">
      {/* Left: Market Indices */}
      <div className="flex items-center gap-6">
        {marketData.indices.map(index => (
          <MarketIndex
            key={index.name}
            name={index.name}
            value={index.value}
            change={index.change}
          />
        ))}
      </div>

      {/* Right: User & Controls */}
      <div className="flex items-center gap-4">
        {/* Market Session */}
        <div className="flex items-center gap-2 text-xs">
          <span className="text-text-tertiary">HK Market</span>
          <span className={cn(
            'font-mono font-medium px-2 py-0.5 rounded',
            marketData.hkStatus === 'OPEN'
              ? 'bg-success/10 text-success'
              : 'bg-neutral/10 text-neutral'
          )}>
            {marketData.hkStatus}
          </span>
        </div>

        <div className="w-px h-4 bg-border-primary" />

        {/* User */}
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-full bg-accent-primary/20 flex items-center justify-center">
            <span className="text-xs font-medium text-accent-primary">JD</span>
          </div>
          <span className="text-sm text-text-secondary">john.doe</span>
        </div>
      </div>
    </header>
  )
}
