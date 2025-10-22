'use client'

import { DashboardLayout } from '@/components/layout/DashboardLayout'
import { SystemControl } from '@/components/live/SystemControl'
import { QueueMonitor } from '@/components/live/QueueMonitor'
import { PositionsTable } from '@/components/live/PositionsTable'
import { MetricCard } from '@/components/metrics/MetricCard'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useEffect, useState } from 'react'

interface SystemMetrics {
  activeStrategies: number
  activePositions: number
  pendingOrders: number
  todayTrades: number
  todayPnl: number
  errorCount: number
}

export default function LiveMonitorPage() {
  const [metrics, setMetrics] = useState<SystemMetrics>({
    activeStrategies: 0,
    activePositions: 0,
    pendingOrders: 0,
    todayTrades: 0,
    todayPnl: 0,
    errorCount: 0,
  })

  const [recentSignals, setRecentSignals] = useState<any[]>([])

  useEffect(() => {
    // Fetch system metrics
    const fetchMetrics = async () => {
      try {
        const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/system/status`)
        const data = await response.json()
        if (data.metrics) {
          setMetrics({
            activeStrategies: data.metrics.active_strategies || 0,
            activePositions: data.metrics.active_positions || 0,
            pendingOrders: data.metrics.pending_orders || 0,
            todayTrades: data.metrics.today_trades || 0,
            todayPnl: data.metrics.today_pnl || 0,
            errorCount: data.metrics.error_count || 0,
          })
        }
      } catch (error) {
        console.error('Failed to fetch metrics:', error)
      }
    }

    fetchMetrics()
    const interval = setInterval(fetchMetrics, 5000)
    return () => clearInterval(interval)
  }, [])

  return (
    <DashboardLayout>
      <div className="p-6 space-y-6">
        {/* Page Header */}
        <div>
          <h1 className="text-xl font-semibold text-text-primary">Live Trading Monitor</h1>
          <p className="text-sm text-text-secondary mt-1">
            Real-time system control and monitoring
          </p>
        </div>

        {/* Metrics Grid */}
        <div className="grid grid-cols-6 gap-4">
          <MetricCard
            label="Active Strategies"
            value={metrics.activeStrategies}
          />
          <MetricCard
            label="Active Positions"
            value={metrics.activePositions}
          />
          <MetricCard
            label="Pending Orders"
            value={metrics.pendingOrders}
          />
          <MetricCard
            label="Today's Trades"
            value={metrics.todayTrades}
          />
          <MetricCard
            label="Today's P&L"
            value={metrics.todayPnl}
            format="currency"
            precision={2}
          />
          <MetricCard
            label="Errors"
            value={metrics.errorCount}
          />
        </div>

        {/* Control Panel */}
        <div className="grid grid-cols-2 gap-6">
          <SystemControl />
          <QueueMonitor />
        </div>

        {/* Positions Table */}
        <PositionsTable />

        {/* Recent Signals */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm uppercase tracking-wider">
              Recent Signals
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-center py-12 text-sm text-text-tertiary">
              No recent signals
            </div>
          </CardContent>
        </Card>

        {/* System Logs (Preview) */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm uppercase tracking-wider">
              System Logs
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="bg-bg-secondary rounded p-4 font-mono text-xs space-y-1 max-h-64 overflow-y-auto">
              <LogEntry
                timestamp="14:32:15"
                level="INFO"
                message="Signal Generator: Analyzing 15 symbols..."
              />
              <LogEntry
                timestamp="14:32:16"
                level="INFO"
                message="Order Executor: Consumed 1 signal from queue"
              />
              <LogEntry
                timestamp="14:32:17"
                level="SUCCESS"
                message="Order submitted: BUY 100 AAPL @ $182.50"
              />
              <LogEntry
                timestamp="14:32:18"
                level="WARNING"
                message="Position limit warning: Approaching max allocation"
              />
            </div>
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  )
}

function LogEntry({
  timestamp,
  level,
  message,
}: {
  timestamp: string
  level: 'INFO' | 'SUCCESS' | 'WARNING' | 'ERROR'
}) {
  const levelColors = {
    INFO: 'text-info',
    SUCCESS: 'text-success',
    WARNING: 'text-warning',
    ERROR: 'text-danger',
  }

  return (
    <div className="flex items-start gap-3 text-text-secondary">
      <span className="text-text-tertiary">{timestamp}</span>
      <span className={`font-medium ${levelColors[level]} w-16`}>{level}</span>
      <span className="flex-1">{message}</span>
    </div>
  )
}
