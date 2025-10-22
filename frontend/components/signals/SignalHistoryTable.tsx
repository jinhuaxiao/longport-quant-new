'use client'

import { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn, formatCurrency, formatReturn, getReturnColor } from '@/lib/utils'
import { RefreshCw, Search, Filter, TrendingUp, TrendingDown, CheckCircle, XCircle, Clock } from 'lucide-react'

interface Signal {
  id: number
  timestamp: string
  symbol: string
  action: 'BUY' | 'SELL'
  price: number
  signal_score: number
  is_executed: boolean
  executed_at?: string
  execution_price?: number
  execution_status?: string
  pnl?: number
  pnl_percent?: number
  indicators?: any
  strategy_name?: string
}

interface SignalHistoryTableProps {
  initialLimit?: number
}

export function SignalHistoryTable({ initialLimit = 50 }: SignalHistoryTableProps) {
  const [signals, setSignals] = useState<Signal[]>([])
  const [loading, setLoading] = useState(false)
  const [totalCount, setTotalCount] = useState(0)

  // 过滤条件
  const [filters, setFilters] = useState({
    symbol: '',
    action: '',
    minScore: '',
    isExecuted: '',
    limit: initialLimit,
    offset: 0,
  })

  const fetchSignals = async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      params.append('limit', filters.limit.toString())
      params.append('offset', filters.offset.toString())

      if (filters.symbol) params.append('symbol', filters.symbol)
      if (filters.action) params.append('action', filters.action)
      if (filters.minScore) params.append('min_score', filters.minScore)
      if (filters.isExecuted) params.append('is_executed', filters.isExecuted)

      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/signals/recent?${params}`)
      const data = await response.json()

      setSignals(data.signals || [])
      setTotalCount(data.total || 0)
    } catch (error) {
      console.error('Failed to fetch signal history:', error)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchSignals()
  }, [filters.limit, filters.offset])

  const handleApplyFilters = () => {
    setFilters({ ...filters, offset: 0 })
    fetchSignals()
  }

  const handleResetFilters = () => {
    setFilters({
      symbol: '',
      action: '',
      minScore: '',
      isExecuted: '',
      limit: initialLimit,
      offset: 0,
    })
  }

  const handleNextPage = () => {
    setFilters(prev => ({ ...prev, offset: prev.offset + prev.limit }))
  }

  const handlePrevPage = () => {
    setFilters(prev => ({ ...prev, offset: Math.max(0, prev.offset - prev.limit) }))
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <div>
          <CardTitle className="text-sm uppercase tracking-wider">
            Signal History
          </CardTitle>
          <div className="text-xs text-text-tertiary mt-1">
            {totalCount.toLocaleString()} total signals
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={fetchSignals}
          disabled={loading}
        >
          <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
        </Button>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Filters */}
        <div className="grid grid-cols-5 gap-2">
          <Input
            placeholder="Symbol..."
            value={filters.symbol}
            onChange={e => setFilters({ ...filters, symbol: e.target.value.toUpperCase() })}
            className="h-8 text-xs"
          />
          <select
            value={filters.action}
            onChange={e => setFilters({ ...filters, action: e.target.value })}
            className="h-8 px-2 bg-bg-secondary border border-border-primary rounded text-xs text-text-primary"
          >
            <option value="">All Actions</option>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
          </select>
          <Input
            type="number"
            placeholder="Min Score..."
            value={filters.minScore}
            onChange={e => setFilters({ ...filters, minScore: e.target.value })}
            className="h-8 text-xs"
          />
          <select
            value={filters.isExecuted}
            onChange={e => setFilters({ ...filters, isExecuted: e.target.value })}
            className="h-8 px-2 bg-bg-secondary border border-border-primary rounded text-xs text-text-primary"
          >
            <option value="">All Status</option>
            <option value="true">Executed</option>
            <option value="false">Not Executed</option>
          </select>
          <div className="flex gap-1">
            <Button
              variant="default"
              size="sm"
              onClick={handleApplyFilters}
              className="h-8 flex-1"
            >
              <Search className="h-3 w-3 mr-1" />
              Search
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleResetFilters}
              className="h-8"
            >
              Reset
            </Button>
          </div>
        </div>

        {/* Table */}
        {signals.length === 0 ? (
          <div className="text-center py-12 text-sm text-text-tertiary">
            No signals found
          </div>
        ) : (
          <div className="border border-border-primary rounded overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-bg-secondary border-b border-border-primary">
                <tr>
                  <th className="px-3 py-2 text-left text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Time
                  </th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Symbol
                  </th>
                  <th className="px-3 py-2 text-center text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Action
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Price
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Score
                  </th>
                  <th className="px-3 py-2 text-center text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Status
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-text-secondary uppercase tracking-wider">
                    P&L
                  </th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Strategy
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-primary">
                {signals.map(signal => (
                  <SignalRow key={signal.id} signal={signal} />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        <div className="flex items-center justify-between">
          <div className="text-xs text-text-tertiary">
            Showing {filters.offset + 1} - {Math.min(filters.offset + filters.limit, totalCount)} of {totalCount}
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handlePrevPage}
              disabled={filters.offset === 0}
              className="h-7"
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleNextPage}
              disabled={filters.offset + filters.limit >= totalCount}
              className="h-7"
            >
              Next
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function SignalRow({ signal }: { signal: Signal }) {
  const timeStr = new Date(signal.timestamp).toLocaleString('en-US', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })

  const getStatusIcon = () => {
    if (!signal.is_executed) {
      return <Clock className="h-3 w-3 text-text-tertiary" />
    }
    if (signal.execution_status === 'success') {
      return <CheckCircle className="h-3 w-3 text-success" />
    }
    return <XCircle className="h-3 w-3 text-danger" />
  }

  const getStatusText = () => {
    if (!signal.is_executed) return 'Pending'
    if (signal.execution_status === 'success') return 'Executed'
    return 'Failed'
  }

  const getStatusColor = () => {
    if (!signal.is_executed) return 'text-text-tertiary'
    if (signal.execution_status === 'success') return 'text-success'
    return 'text-danger'
  }

  return (
    <tr className="hover:bg-bg-secondary/50">
      <td className="px-3 py-2">
        <span className="text-xs font-mono text-text-tertiary">
          {timeStr}
        </span>
      </td>
      <td className="px-3 py-2">
        <span className="font-mono font-medium text-text-primary">
          {signal.symbol}
        </span>
      </td>
      <td className="px-3 py-2 text-center">
        <span className={cn(
          'px-2 py-0.5 rounded text-xs font-medium',
          signal.action === 'BUY'
            ? 'bg-long/10 text-long'
            : 'bg-short/10 text-short'
        )}>
          {signal.action}
        </span>
      </td>
      <td className="px-3 py-2 text-right">
        <span className="font-mono tabular-nums text-text-primary">
          {signal.price.toFixed(2)}
        </span>
      </td>
      <td className="px-3 py-2 text-right">
        <div className="flex items-center justify-end gap-1">
          <span className={cn(
            'font-mono tabular-nums',
            signal.signal_score >= 70 ? 'text-success' :
            signal.signal_score >= 50 ? 'text-warning' : 'text-text-secondary'
          )}>
            {signal.signal_score.toFixed(1)}
          </span>
          {signal.signal_score >= 70 && <TrendingUp className="h-3 w-3 text-success" />}
        </div>
      </td>
      <td className="px-3 py-2">
        <div className="flex items-center justify-center gap-1">
          {getStatusIcon()}
          <span className={cn('text-xs', getStatusColor())}>
            {getStatusText()}
          </span>
        </div>
      </td>
      <td className="px-3 py-2 text-right">
        {signal.pnl !== undefined && signal.pnl !== null ? (
          <div>
            <div className={cn(
              'font-mono tabular-nums text-xs',
              getReturnColor(signal.pnl)
            )}>
              {signal.pnl >= 0 ? '+' : ''}{formatCurrency(signal.pnl)}
            </div>
            {signal.pnl_percent !== undefined && (
              <div className={cn(
                'font-mono tabular-nums text-xs',
                getReturnColor(signal.pnl)
              )}>
                {formatReturn(signal.pnl_percent)}
              </div>
            )}
          </div>
        ) : (
          <span className="text-xs text-text-tertiary">-</span>
        )}
      </td>
      <td className="px-3 py-2">
        <span className="text-xs text-text-secondary">
          {signal.strategy_name || 'Default'}
        </span>
      </td>
    </tr>
  )
}
