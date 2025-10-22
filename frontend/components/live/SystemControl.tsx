'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { Play, Square, Pause, PlayCircle, AlertTriangle } from 'lucide-react'

interface SystemStatus {
  status: 'running' | 'stopped' | 'paused'
  signalGenerator: ProcessStatus
  orderExecutor: ProcessStatus
}

interface ProcessStatus {
  running: boolean
  pid?: number
  uptime?: string
  lastActivity?: string
}

export function SystemControl() {
  const [systemStatus, setSystemStatus] = useState<SystemStatus>({
    status: 'stopped',
    signalGenerator: { running: false },
    orderExecutor: { running: false },
  })

  const [loading, setLoading] = useState(false)

  const handleStartSystem = async () => {
    setLoading(true)
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/system/control/start`, {
        method: 'POST',
      })
      const data = await response.json()
      console.log('System started:', data)
      // Update status from response
      fetchSystemStatus()
    } catch (error) {
      console.error('Failed to start system:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleStopSystem = async () => {
    if (!confirm('Are you sure you want to stop the trading system? All running processes will be terminated.')) {
      return
    }

    setLoading(true)
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/system/control/stop`, {
        method: 'POST',
      })
      const data = await response.json()
      console.log('System stopped:', data)
      fetchSystemStatus()
    } catch (error) {
      console.error('Failed to stop system:', error)
    } finally {
      setLoading(false)
    }
  }

  const handlePauseSystem = async () => {
    setLoading(true)
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/system/control/pause`, {
        method: 'POST',
      })
      const data = await response.json()
      console.log('System paused:', data)
      fetchSystemStatus()
    } catch (error) {
      console.error('Failed to pause system:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleResumeSystem = async () => {
    setLoading(true)
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/system/control/resume`, {
        method: 'POST',
      })
      const data = await response.json()
      console.log('System resumed:', data)
      fetchSystemStatus()
    } catch (error) {
      console.error('Failed to resume system:', error)
    } finally {
      setLoading(false)
    }
  }

  const fetchSystemStatus = async () => {
    try {
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/system/status`)
      const data = await response.json()
      // Update system status from API
      setSystemStatus(data)
    } catch (error) {
      console.error('Failed to fetch system status:', error)
    }
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'running':
        return 'text-success'
      case 'paused':
        return 'text-warning'
      case 'stopped':
        return 'text-text-tertiary'
      default:
        return 'text-text-secondary'
    }
  }

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'running':
        return 'bg-success/10 text-success'
      case 'paused':
        return 'bg-warning/10 text-warning'
      case 'stopped':
        return 'bg-neutral/10 text-neutral'
      default:
        return 'bg-neutral/10 text-neutral'
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm uppercase tracking-wider">System Control</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* System Status */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <span className="text-xs text-text-secondary uppercase">System Status</span>
            <div className={cn('px-3 py-1 rounded text-xs font-medium font-mono', getStatusBadge(systemStatus.status))}>
              {systemStatus.status.toUpperCase()}
            </div>
          </div>

          {/* Process Status */}
          <div className="space-y-2">
            <ProcessStatusRow
              name="Signal Generator"
              status={systemStatus.signalGenerator}
            />
            <ProcessStatusRow
              name="Order Executor"
              status={systemStatus.orderExecutor}
            />
          </div>
        </div>

        {/* Control Buttons */}
        <div className="grid grid-cols-2 gap-2">
          <Button
            variant="default"
            onClick={handleStartSystem}
            disabled={loading || systemStatus.status === 'running'}
            className="w-full"
          >
            <Play className="h-4 w-4 mr-2" />
            Start System
          </Button>

          <Button
            variant="danger"
            onClick={handleStopSystem}
            disabled={loading || systemStatus.status === 'stopped'}
            className="w-full"
          >
            <Square className="h-4 w-4 mr-2" />
            Stop System
          </Button>

          {systemStatus.status === 'running' && (
            <Button
              variant="outline"
              onClick={handlePauseSystem}
              disabled={loading}
              className="w-full"
            >
              <Pause className="h-4 w-4 mr-2" />
              Pause Trading
            </Button>
          )}

          {systemStatus.status === 'paused' && (
            <Button
              variant="success"
              onClick={handleResumeSystem}
              disabled={loading}
              className="w-full"
            >
              <PlayCircle className="h-4 w-4 mr-2" />
              Resume Trading
            </Button>
          )}
        </div>

        {/* Emergency Stop Warning */}
        {systemStatus.status === 'running' && (
          <div className="bg-warning/10 border border-warning/20 rounded p-3">
            <div className="flex items-start gap-2">
              <AlertTriangle className="h-4 w-4 text-warning mt-0.5" />
              <div className="text-xs text-text-secondary">
                <div className="font-medium text-warning mb-1">Emergency Stop</div>
                Clicking "Stop System" will immediately terminate all trading processes.
                Pending orders may not be cancelled.
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function ProcessStatusRow({ name, status }: { name: string; status: ProcessStatus }) {
  return (
    <div className="flex items-center justify-between p-2 bg-bg-secondary rounded border border-border-primary">
      <div className="flex items-center gap-2">
        <div className={cn(
          'w-2 h-2 rounded-full',
          status.running ? 'bg-success animate-pulse' : 'bg-text-disabled'
        )} />
        <span className="text-sm text-text-primary">{name}</span>
      </div>
      <div className="flex items-center gap-3 text-xs">
        {status.pid && (
          <span className="text-text-tertiary font-mono">
            PID: {status.pid}
          </span>
        )}
        {status.uptime && (
          <span className="text-text-tertiary font-mono">
            {status.uptime}
          </span>
        )}
        <span className={cn(
          'font-medium',
          status.running ? 'text-success' : 'text-text-tertiary'
        )}>
          {status.running ? 'RUNNING' : 'STOPPED'}
        </span>
      </div>
    </div>
  )
}
