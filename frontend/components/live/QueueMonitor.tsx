'use client'

import { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { Activity, Trash2, RefreshCw, AlertCircle } from 'lucide-react'

interface QueueStats {
  pending: number
  processing: number
  failed: number
  totalProcessed: number
  successRate: number
}

export function QueueMonitor() {
  const [stats, setStats] = useState<QueueStats>({
    pending: 0,
    processing: 0,
    failed: 0,
    totalProcessed: 0,
    successRate: 100,
  })
  const [loading, setLoading] = useState(false)

  const fetchQueueStats = async () => {
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/queue/stats`)
      const data = await response.json()
      setStats(data)
    } catch (error) {
      console.error('Failed to fetch queue stats:', error)
    }
  }

  const handleClearQueue = async (queueType: 'pending' | 'processing' | 'failed') => {
    const confirmMessage = queueType === 'failed'
      ? 'Clear all failed signals?'
      : `DANGER: Clear all ${queueType} signals? This cannot be undone!`

    if (!confirm(confirmMessage)) {
      return
    }

    setLoading(true)
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/queue/clear/${queueType}`, {
        method: 'POST',
      })
      await response.json()
      await fetchQueueStats()
    } catch (error) {
      console.error(`Failed to clear ${queueType} queue:`, error)
    } finally {
      setLoading(false)
    }
  }

  const handleRetryFailed = async () => {
    setLoading(true)
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/queue/retry-failed`, {
        method: 'POST',
      })
      await response.json()
      await fetchQueueStats()
    } catch (error) {
      console.error('Failed to retry failed signals:', error)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchQueueStats()
    const interval = setInterval(fetchQueueStats, 3000) // Refresh every 3 seconds
    return () => clearInterval(interval)
  }, [])

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-sm uppercase tracking-wider">
          Redis Queue Status
        </CardTitle>
        <Button
          variant="ghost"
          size="icon"
          className="h-7 w-7"
          onClick={fetchQueueStats}
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Queue Stats */}
        <div className="grid grid-cols-3 gap-3">
          <QueueStatCard
            label="Pending"
            count={stats.pending}
            color="text-info"
            icon={<Activity className="h-4 w-4" />}
            onClear={() => handleClearQueue('pending')}
            loading={loading}
          />
          <QueueStatCard
            label="Processing"
            count={stats.processing}
            color="text-warning"
            icon={<Activity className="h-4 w-4 animate-pulse" />}
            onClear={() => handleClearQueue('processing')}
            loading={loading}
          />
          <QueueStatCard
            label="Failed"
            count={stats.failed}
            color="text-danger"
            icon={<AlertCircle className="h-4 w-4" />}
            onClear={() => handleClearQueue('failed')}
            loading={loading}
          />
        </div>

        {/* Performance Metrics */}
        <div className="bg-bg-secondary border border-border-primary rounded p-3">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <div className="text-xs text-text-tertiary mb-1">Total Processed</div>
              <div className="text-lg font-mono tabular-nums text-text-primary">
                {stats.totalProcessed.toLocaleString()}
              </div>
            </div>
            <div>
              <div className="text-xs text-text-tertiary mb-1">Success Rate</div>
              <div className={cn(
                'text-lg font-mono tabular-nums',
                stats.successRate >= 95 ? 'text-success' : stats.successRate >= 80 ? 'text-warning' : 'text-danger'
              )}>
                {stats.successRate.toFixed(1)}%
              </div>
            </div>
          </div>
        </div>

        {/* Actions */}
        {stats.failed > 0 && (
          <div className="bg-warning/10 border border-warning/20 rounded p-3">
            <div className="flex items-center justify-between">
              <div className="text-xs text-text-secondary">
                <span className="font-medium text-warning">{stats.failed}</span> signals failed
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={handleRetryFailed}
                disabled={loading}
                className="h-7"
              >
                <RefreshCw className="h-3 w-3 mr-1" />
                Retry All
              </Button>
            </div>
          </div>
        )}

        {/* Queue Management */}
        <div className="pt-2 border-t border-border-primary">
          <div className="text-xs text-text-tertiary mb-2 uppercase tracking-wider">
            Queue Management
          </div>
          <div className="text-xs text-text-secondary space-y-1">
            <div className="font-mono bg-bg-tertiary p-2 rounded">
              redis-cli ZCARD trading:signals
            </div>
            <div className="text-text-tertiary">
              Monitor queue size in real-time
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function QueueStatCard({
  label,
  count,
  color,
  icon,
  onClear,
  loading,
}: {
  label: string
  count: number
  color: string
  icon: React.ReactNode
  onClear: () => void
  loading: boolean
}) {
  return (
    <div className="bg-bg-secondary border border-border-primary rounded p-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-text-tertiary uppercase">{label}</span>
        <div className={color}>{icon}</div>
      </div>
      <div className={cn('text-2xl font-mono tabular-nums mb-2', color)}>
        {count}
      </div>
      {count > 0 && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onClear}
          disabled={loading}
          className="w-full h-6 text-xs text-danger hover:text-danger"
        >
          <Trash2 className="h-3 w-3 mr-1" />
          Clear
        </Button>
      )}
    </div>
  )
}
