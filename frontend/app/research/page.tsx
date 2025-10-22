'use client'

import { useState } from 'react'
import { DashboardLayout } from '@/components/layout/DashboardLayout'
import { SymbolSelector } from '@/components/research/SymbolSelector'
import { BacktestWizard } from '@/components/research/BacktestWizard'
import { Button } from '@/components/ui/button'
import { Clock, CheckCircle, XCircle } from 'lucide-react'

interface Task {
  id: string
  name: string
  status: 'running' | 'completed' | 'failed'
  progress: number
  createdAt: Date
}

export default function ResearchPage() {
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>([])
  const [tasks, setTasks] = useState<Task[]>([
    { id: '1', name: 'MA Cross - AAPL, MSFT', status: 'completed', progress: 100, createdAt: new Date() },
    { id: '2', name: 'RSI - Tech Portfolio', status: 'running', progress: 45, createdAt: new Date() },
  ])

  const handleBacktestSubmit = (config: any) => {
    console.log('Submitting backtest:', config)
    // In production, send to API
    const newTask: Task = {
      id: Date.now().toString(),
      name: `${config.strategy} - ${config.symbols.join(', ')}`,
      status: 'running',
      progress: 0,
      createdAt: new Date(),
    }
    setTasks([newTask, ...tasks])
  }

  return (
    <DashboardLayout>
      <div className="h-full flex">
        {/* Left: Symbol Selector */}
        <div className="w-80">
          <SymbolSelector
            selected={selectedSymbols}
            onChange={setSelectedSymbols}
          />
        </div>

        {/* Center: Backtest Wizard */}
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="h-12 border-b border-border-primary flex items-center justify-between px-6 bg-bg-secondary">
            <h1 className="text-lg font-semibold text-text-primary">Backtest Configuration</h1>
            <div className="flex items-center gap-2 text-xs text-text-tertiary">
              <span>Last saved: 2 minutes ago</span>
            </div>
          </div>

          <div className="flex-1 overflow-hidden">
            <BacktestWizard
              symbols={selectedSymbols}
              onSubmit={handleBacktestSubmit}
            />
          </div>
        </div>

        {/* Right: Task Queue */}
        <div className="w-90 border-l border-border-primary bg-bg-secondary">
          <TaskQueue tasks={tasks} />
        </div>
      </div>
    </DashboardLayout>
  )
}

function TaskQueue({ tasks }: { tasks: Task[] }) {
  const runningTasks = tasks.filter(t => t.status === 'running')
  const completedTasks = tasks.filter(t => t.status === 'completed')
  const failedTasks = tasks.filter(t => t.status === 'failed')

  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-border-primary">
        <h2 className="text-sm font-semibold text-text-primary uppercase tracking-wider">
          Task Queue
        </h2>
      </div>

      <div className="flex-1 overflow-auto p-4 space-y-4">
        {/* Running */}
        {runningTasks.length > 0 && (
          <div>
            <div className="flex items-center gap-2 mb-2">
              <Clock className="h-4 w-4 text-warning" />
              <span className="text-xs font-medium text-text-secondary uppercase">
                Running ({runningTasks.length})
              </span>
            </div>
            <div className="space-y-2">
              {runningTasks.map(task => (
                <TaskItem key={task.id} task={task} />
              ))}
            </div>
          </div>
        )}

        {/* Completed */}
        {completedTasks.length > 0 && (
          <div>
            <div className="flex items-center gap-2 mb-2">
              <CheckCircle className="h-4 w-4 text-success" />
              <span className="text-xs font-medium text-text-secondary uppercase">
                Completed ({completedTasks.length})
              </span>
            </div>
            <div className="space-y-2">
              {completedTasks.slice(0, 5).map(task => (
                <TaskItem key={task.id} task={task} />
              ))}
            </div>
          </div>
        )}

        {/* Failed */}
        {failedTasks.length > 0 && (
          <div>
            <div className="flex items-center gap-2 mb-2">
              <XCircle className="h-4 w-4 text-danger" />
              <span className="text-xs font-medium text-text-secondary uppercase">
                Failed ({failedTasks.length})
              </span>
            </div>
            <div className="space-y-2">
              {failedTasks.map(task => (
                <TaskItem key={task.id} task={task} />
              ))}
            </div>
          </div>
        )}

        {tasks.length === 0 && (
          <div className="text-center py-12 text-sm text-text-tertiary">
            No tasks yet
          </div>
        )}
      </div>

      <div className="p-4 border-t border-border-primary">
        <Button variant="outline" size="sm" className="w-full">
          View All Tasks
        </Button>
      </div>
    </div>
  )
}

function TaskItem({ task }: { task: Task }) {
  const statusColors = {
    running: 'bg-warning/10 border-warning/20',
    completed: 'bg-success/10 border-success/20',
    failed: 'bg-danger/10 border-danger/20',
  }

  return (
    <div className={`border rounded p-3 ${statusColors[task.status]}`}>
      <div className="text-sm text-text-primary font-medium mb-2">
        {task.name}
      </div>

      {task.status === 'running' && (
        <>
          <div className="h-1 bg-bg-tertiary rounded-full overflow-hidden mb-2">
            <div
              className="h-full bg-warning transition-all duration-300"
              style={{ width: `${task.progress}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-xs">
            <span className="text-text-secondary">Processing...</span>
            <span className="text-text-tertiary font-mono tabular-nums">
              {task.progress}%
            </span>
          </div>
        </>
      )}

      {task.status === 'completed' && (
        <Button variant="ghost" size="sm" className="w-full h-7 text-xs mt-1">
          View Results
        </Button>
      )}

      {task.status === 'failed' && (
        <div className="text-xs text-danger mt-1">
          Error: Insufficient data
        </div>
      )}
    </div>
  )
}
