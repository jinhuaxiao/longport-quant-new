import { DashboardLayout } from '@/components/layout/DashboardLayout'
import { MetricCard } from '@/components/metrics/MetricCard'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'

export default function HomePage() {
  return (
    <DashboardLayout>
      <div className="p-6 space-y-6">
        {/* Page Header */}
        <div>
          <h1 className="text-xl font-semibold text-text-primary">Dashboard Overview</h1>
          <p className="text-sm text-text-secondary mt-1">
            System performance and activity summary
          </p>
        </div>

        {/* System Metrics */}
        <div className="grid grid-cols-6 gap-4">
          <MetricCard
            label="Active Strategies"
            value={12}
            change={8.3}
            changeLabel="vs Last Week"
          />
          <MetricCard
            label="Total P&L (YTD)"
            value={45678.90}
            format="currency"
            change={12.5}
            changeLabel="vs Last Month"
          />
          <MetricCard
            label="Win Rate"
            value={64.5}
            format="percent"
            precision={1}
          />
          <MetricCard
            label="Sharpe Ratio"
            value={1.85}
            precision={2}
          />
          <MetricCard
            label="Max Drawdown"
            value={-12.3}
            format="percent"
            precision={1}
          />
          <MetricCard
            label="Active Positions"
            value={8}
            change={-20.0}
            changeLabel="vs Yesterday"
          />
        </div>

        {/* Recent Activity */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm uppercase tracking-wider">
              Recent Activity
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              <ActivityItem
                time="14:32:15"
                type="BACKTEST"
                message="Backtest completed: MA Crossover Strategy"
                status="success"
              />
              <ActivityItem
                time="13:45:20"
                type="TRADE"
                message="Order filled: BUY 100 AAPL @ $182.50"
                status="success"
              />
              <ActivityItem
                time="12:18:05"
                type="DATA"
                message="Historical data sync completed for 50 symbols"
                status="success"
              />
              <ActivityItem
                time="11:22:30"
                type="ALERT"
                message="Position limit warning: NVDA approaching max allocation"
                status="warning"
              />
            </div>
          </CardContent>
        </Card>
      </div>
    </DashboardLayout>
  )
}

function ActivityItem({
  time,
  type,
  message,
  status,
}: {
  time: string
  type: string
  message: string
  status: 'success' | 'warning' | 'error'
}) {
  const statusColors = {
    success: 'bg-success/10 text-success',
    warning: 'bg-warning/10 text-warning',
    error: 'bg-danger/10 text-danger',
  }

  return (
    <div className="flex items-start gap-3 py-2 border-b border-border-primary last:border-0">
      <span className="text-xs font-mono text-text-tertiary">{time}</span>
      <span className={`text-xs font-medium px-2 py-0.5 rounded ${statusColors[status]}`}>
        {type}
      </span>
      <span className="flex-1 text-sm text-text-secondary">{message}</span>
    </div>
  )
}
