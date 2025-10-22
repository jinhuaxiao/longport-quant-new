'use client'

import { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { cn, formatCurrency, formatReturn, getReturnColor } from '@/lib/utils'
import { RefreshCw, TrendingUp, TrendingDown } from 'lucide-react'

interface Position {
  symbol: string
  quantity: number
  entryPrice: number
  currentPrice: number
  marketValue: number
  unrealizedPnl: number
  unrealizedPnlPercent: number
  realizedPnl: number
  holdingPeriod: string
  riskLevel: 'low' | 'medium' | 'high'
  stopLoss?: number
  takeProfit?: number
}

export function PositionsTable() {
  const [positions, setPositions] = useState<Position[]>([])
  const [loading, setLoading] = useState(false)

  const fetchPositions = async () => {
    setLoading(true)
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/positions`)
      const data = await response.json()

      // Convert API response to Position array
      const positionsArray = Object.entries(data).map(([symbol, pos]: [string, any]) => ({
        symbol,
        quantity: pos.quantity,
        entryPrice: pos.entry_price,
        currentPrice: pos.current_price,
        marketValue: pos.market_value,
        unrealizedPnl: pos.unrealized_pnl,
        unrealizedPnlPercent: pos.pnl_percent,
        realizedPnl: pos.realized_pnl,
        holdingPeriod: pos.holding_period,
        riskLevel: pos.risk_level,
        stopLoss: pos.stop_loss,
        takeProfit: pos.take_profit,
      }))

      setPositions(positionsArray)
    } catch (error) {
      console.error('Failed to fetch positions:', error)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchPositions()
    const interval = setInterval(fetchPositions, 5000) // Refresh every 5 seconds
    return () => clearInterval(interval)
  }, [])

  const totalUnrealizedPnl = positions.reduce((sum, pos) => sum + pos.unrealizedPnl, 0)
  const totalMarketValue = positions.reduce((sum, pos) => sum + pos.marketValue, 0)

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <div>
          <CardTitle className="text-sm uppercase tracking-wider">
            Active Positions
          </CardTitle>
          <div className="text-xs text-text-tertiary mt-1">
            {positions.length} position{positions.length !== 1 ? 's' : ''} · Market Value: {formatCurrency(totalMarketValue)}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="text-right">
            <div className="text-xs text-text-tertiary">Unrealized P&L</div>
            <div className={cn(
              'text-sm font-mono tabular-nums font-medium',
              getReturnColor(totalUnrealizedPnl)
            )}>
              {totalUnrealizedPnl >= 0 ? '+' : ''}{formatCurrency(totalUnrealizedPnl)}
            </div>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={fetchPositions}
            disabled={loading}
          >
            <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {positions.length === 0 ? (
          <div className="text-center py-12 text-sm text-text-tertiary">
            No active positions
          </div>
        ) : (
          <div className="border border-border-primary rounded overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-bg-secondary border-b border-border-primary">
                <tr>
                  <th className="px-3 py-2 text-left text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Symbol
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Qty
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Entry
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Current
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Value
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-text-secondary uppercase tracking-wider">
                    P&L
                  </th>
                  <th className="px-3 py-2 text-center text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Risk
                  </th>
                  <th className="px-3 py-2 text-center text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Period
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-primary">
                {positions.map(position => (
                  <tr key={position.symbol} className="hover:bg-bg-secondary/50">
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-2">
                        <span className="font-mono font-medium text-text-primary">
                          {position.symbol}
                        </span>
                        {position.unrealizedPnl >= 0 ? (
                          <TrendingUp className="h-3 w-3 text-long" />
                        ) : (
                          <TrendingDown className="h-3 w-3 text-short" />
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className="font-mono tabular-nums text-text-primary">
                        {position.quantity.toLocaleString()}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className="font-mono tabular-nums text-text-secondary">
                        {position.entryPrice.toFixed(2)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className="font-mono tabular-nums text-text-primary">
                        {position.currentPrice.toFixed(2)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className="font-mono tabular-nums text-text-primary">
                        {formatCurrency(position.marketValue)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div>
                        <div className={cn(
                          'font-mono tabular-nums font-medium',
                          getReturnColor(position.unrealizedPnl)
                        )}>
                          {position.unrealizedPnl >= 0 ? '+' : ''}{formatCurrency(position.unrealizedPnl)}
                        </div>
                        <div className={cn(
                          'text-xs font-mono tabular-nums',
                          getReturnColor(position.unrealizedPnl)
                        )}>
                          {formatReturn(position.unrealizedPnlPercent)}
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-2 text-center">
                      <span className={cn(
                        'px-2 py-0.5 rounded text-xs font-medium uppercase',
                        position.riskLevel === 'low' && 'bg-success/10 text-success',
                        position.riskLevel === 'medium' && 'bg-warning/10 text-warning',
                        position.riskLevel === 'high' && 'bg-danger/10 text-danger'
                      )}>
                        {position.riskLevel}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-center">
                      <span className="text-xs text-text-tertiary font-mono">
                        {position.holdingPeriod}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
