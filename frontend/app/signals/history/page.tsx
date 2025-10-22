'use client'

import { useState, useEffect } from 'react'
import { DashboardLayout } from '@/components/layout/DashboardLayout'
import { SignalHistoryTable } from '@/components/signals/SignalHistoryTable'
import { MetricCard } from '@/components/metrics/MetricCard'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

interface SignalStats {
  total_signals: number
  buy_signals: number
  sell_signals: number
  executed_signals: number
  execution_rate: number
  average_score: number
}

interface SignalPerformance {
  total_executed: number
  profitable_count: number
  loss_count: number
  win_rate: number
  total_pnl: number
  average_pnl: number
}

export default function SignalHistoryPage() {
  const [stats, setStats] = useState<SignalStats | null>(null)
  const [performance, setPerformance] = useState<SignalPerformance | null>(null)
  const [days, setDays] = useState(30)

  const fetchStats = async () => {
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/signals/stats?days=${days}`)
      const data = await response.json()

      setStats(data.stats)
      setPerformance(data.performance)
    } catch (error) {
      console.error('Failed to fetch signal stats:', error)
    }
  }

  useEffect(() => {
    fetchStats()
    const interval = setInterval(fetchStats, 30000) // Refresh every 30s
    return () => clearInterval(interval)
  }, [days])

  return (
    <DashboardLayout>
      <div className="p-6 space-y-6">
        {/* Page Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-text-primary">Signal History & Analytics</h1>
            <p className="text-sm text-text-secondary mt-1">
              Historical signal records and performance analysis
            </p>
          </div>

          {/* Time Range Selector */}
          <div className="flex items-center gap-2">
            <span className="text-xs text-text-tertiary">Period:</span>
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="h-8 px-3 bg-bg-secondary border border-border-primary rounded text-sm text-text-primary"
            >
              <option value={7}>Last 7 Days</option>
              <option value={30}>Last 30 Days</option>
              <option value={90}>Last 90 Days</option>
              <option value={180}>Last 6 Months</option>
              <option value={365}>Last Year</option>
            </select>
          </div>
        </div>

        {/* Stats Grid */}
        {stats && (
          <div className="grid grid-cols-6 gap-4">
            <MetricCard
              label="Total Signals"
              value={stats.total_signals}
            />
            <MetricCard
              label="Buy Signals"
              value={stats.buy_signals}
            />
            <MetricCard
              label="Sell Signals"
              value={stats.sell_signals}
            />
            <MetricCard
              label="Executed"
              value={stats.executed_signals}
            />
            <MetricCard
              label="Execution Rate"
              value={stats.execution_rate}
              format="percent"
              precision={1}
            />
            <MetricCard
              label="Avg Score"
              value={stats.average_score}
              precision={1}
            />
          </div>
        )}

        {/* Performance Metrics */}
        {performance && (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm uppercase tracking-wider">
                Performance Analysis (Last {days} Days)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-4 gap-6">
                <div>
                  <div className="text-xs text-text-tertiary mb-1 uppercase tracking-wider">
                    Win Rate
                  </div>
                  <div className="text-3xl font-mono tabular-nums text-success">
                    {performance.win_rate.toFixed(1)}%
                  </div>
                  <div className="text-xs text-text-tertiary mt-1">
                    {performance.profitable_count} / {performance.total_executed} trades
                  </div>
                </div>

                <div>
                  <div className="text-xs text-text-tertiary mb-1 uppercase tracking-wider">
                    Total P&L
                  </div>
                  <div className={`text-3xl font-mono tabular-nums ${performance.total_pnl >= 0 ? 'text-long' : 'text-short'}`}>
                    {performance.total_pnl >= 0 ? '+' : ''}${performance.total_pnl.toFixed(2)}
                  </div>
                  <div className="text-xs text-text-tertiary mt-1">
                    Across all executed signals
                  </div>
                </div>

                <div>
                  <div className="text-xs text-text-tertiary mb-1 uppercase tracking-wider">
                    Avg P&L per Trade
                  </div>
                  <div className={`text-3xl font-mono tabular-nums ${performance.average_pnl >= 0 ? 'text-long' : 'text-short'}`}>
                    {performance.average_pnl >= 0 ? '+' : ''}${performance.average_pnl.toFixed(2)}
                  </div>
                  <div className="text-xs text-text-tertiary mt-1">
                    Per executed signal
                  </div>
                </div>

                <div>
                  <div className="text-xs text-text-tertiary mb-1 uppercase tracking-wider">
                    Trades Breakdown
                  </div>
                  <div className="flex items-baseline gap-3">
                    <div>
                      <div className="text-lg font-mono tabular-nums text-success">
                        {performance.profitable_count}
                      </div>
                      <div className="text-xs text-text-tertiary">Wins</div>
                    </div>
                    <div className="text-text-tertiary">/</div>
                    <div>
                      <div className="text-lg font-mono tabular-nums text-short">
                        {performance.loss_count}
                      </div>
                      <div className="text-xs text-text-tertiary">Losses</div>
                    </div>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Tabs */}
        <Tabs defaultValue="history">
          <TabsList>
            <TabsTrigger value="history">Signal History</TabsTrigger>
            <TabsTrigger value="analytics">Analytics</TabsTrigger>
            <TabsTrigger value="top-performers">Top Performers</TabsTrigger>
          </TabsList>

          <TabsContent value="history" className="mt-4">
            <SignalHistoryTable initialLimit={50} />
          </TabsContent>

          <TabsContent value="analytics" className="mt-4">
            <SignalAnalyticsView days={days} />
          </TabsContent>

          <TabsContent value="top-performers" className="mt-4">
            <TopPerformersView days={days} />
          </TabsContent>
        </Tabs>
      </div>
    </DashboardLayout>
  )
}

function SignalAnalyticsView({ days }: { days: number }) {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm uppercase tracking-wider">
            Signal Distribution
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-center py-12 text-sm text-text-tertiary">
            Chart visualization coming soon...
            <div className="mt-2 text-xs">
              Will show signal distribution by time, score, and outcome
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm uppercase tracking-wider">
            Score vs Performance Correlation
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-center py-12 text-sm text-text-tertiary">
            Scatter plot coming soon...
            <div className="mt-2 text-xs">
              Will correlate signal scores with actual P&L outcomes
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function TopPerformersView({ days }: { days: number }) {
  const [performers, setPerformers] = useState<any[]>([])
  const [sortBy, setSortBy] = useState('win_rate')

  const fetchTopPerformers = async () => {
    try {
      const response = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL}/api/signals/top-performers?days=${days}&limit=20&sort_by=${sortBy}`
      )
      const data = await response.json()
      setPerformers(data.top_performers || [])
    } catch (error) {
      console.error('Failed to fetch top performers:', error)
    }
  }

  useEffect(() => {
    fetchTopPerformers()
  }, [days, sortBy])

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-sm uppercase tracking-wider">
          Top Performing Symbols
        </CardTitle>
        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value)}
          className="h-8 px-3 bg-bg-secondary border border-border-primary rounded text-sm text-text-primary"
        >
          <option value="win_rate">Win Rate</option>
          <option value="total_pnl">Total P&L</option>
          <option value="signal_count">Signal Count</option>
        </select>
      </CardHeader>
      <CardContent>
        {performers.length === 0 ? (
          <div className="text-center py-12 text-sm text-text-tertiary">
            No performance data available
          </div>
        ) : (
          <div className="border border-border-primary rounded overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-bg-secondary border-b border-border-primary">
                <tr>
                  <th className="px-3 py-2 text-left text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Rank
                  </th>
                  <th className="px-3 py-2 text-left text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Symbol
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Signals
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Win Rate
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Total P&L
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-medium text-text-secondary uppercase tracking-wider">
                    Avg Score
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-primary">
                {performers.map((perf, index) => (
                  <tr key={perf.symbol} className="hover:bg-bg-secondary/50">
                    <td className="px-3 py-2">
                      <span className="font-mono text-text-tertiary">
                        #{index + 1}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <span className="font-mono font-medium text-text-primary">
                        {perf.symbol}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className="font-mono tabular-nums text-text-primary">
                        {perf.signal_count}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className="font-mono tabular-nums text-success">
                        {perf.win_rate.toFixed(1)}%
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className={`font-mono tabular-nums ${perf.total_pnl >= 0 ? 'text-long' : 'text-short'}`}>
                        {perf.total_pnl >= 0 ? '+' : ''}${perf.total_pnl.toFixed(2)}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className="font-mono tabular-nums text-text-secondary">
                        {perf.avg_score.toFixed(1)}
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
