'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { ArrowLeft, ArrowRight, Play } from 'lucide-react'

interface BacktestConfig {
  symbols: string[]
  startDate: string
  endDate: string
  frequency: '1d' | '1h' | '15m'
  strategy?: string
  strategyParams?: Record<string, any>
  initialCash: number
  commission: number
  slippage: number
}

const STEPS = [
  { id: 1, title: 'Data Preparation' },
  { id: 2, title: 'Strategy Selection' },
  { id: 3, title: 'Parameter Configuration' },
  { id: 4, title: 'Risk Control' },
]

interface BacktestWizardProps {
  symbols: string[]
  onSubmit: (config: BacktestConfig) => void
}

export function BacktestWizard({ symbols, onSubmit }: BacktestWizardProps) {
  const [step, setStep] = useState(1)
  const [config, setConfig] = useState<BacktestConfig>({
    symbols: symbols,
    startDate: '2022-01-01',
    endDate: new Date().toISOString().split('T')[0],
    frequency: '1d',
    initialCash: 100000,
    commission: 0.03,
    slippage: 0.1,
  })

  const handleNext = () => {
    if (step < STEPS.length) {
      setStep(step + 1)
    }
  }

  const handlePrevious = () => {
    if (step > 1) {
      setStep(step - 1)
    }
  }

  const handleSubmit = () => {
    onSubmit(config)
  }

  return (
    <div className="h-full flex flex-col">
      {/* Step Indicator */}
      <div className="px-6 py-4 border-b border-border-primary">
        <div className="flex items-center justify-between">
          {STEPS.map((s, idx) => (
            <div key={s.id} className="flex items-center">
              <div className="flex items-center gap-2">
                <div className={cn(
                  'flex items-center justify-center w-8 h-8 rounded-full border-2 text-sm font-medium',
                  step >= s.id
                    ? 'border-accent-primary bg-accent-primary text-white'
                    : 'border-border-secondary text-text-tertiary'
                )}>
                  {s.id}
                </div>
                <div className={cn(
                  'text-sm font-medium',
                  step >= s.id ? 'text-text-primary' : 'text-text-tertiary'
                )}>
                  {s.title}
                </div>
              </div>
              {idx < STEPS.length - 1 && (
                <div className={cn(
                  'mx-4 h-0.5 w-12',
                  step > s.id ? 'bg-accent-primary' : 'bg-border-primary'
                )} />
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto px-6 py-6">
        {step === 1 && <DataPrepStep config={config} onChange={setConfig} />}
        {step === 2 && <StrategySelectStep config={config} onChange={setConfig} />}
        {step === 3 && <ParameterConfigStep config={config} onChange={setConfig} />}
        {step === 4 && <RiskControlStep config={config} onChange={setConfig} />}
      </div>

      {/* Actions */}
      <div className="border-t border-border-primary px-6 py-4 flex justify-between">
        <Button
          variant="outline"
          onClick={handlePrevious}
          disabled={step === 1}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          Previous
        </Button>

        {step < STEPS.length ? (
          <Button onClick={handleNext}>
            Next
            <ArrowRight className="h-4 w-4 ml-2" />
          </Button>
        ) : (
          <Button onClick={handleSubmit}>
            <Play className="h-4 w-4 mr-2" />
            Execute Backtest
          </Button>
        )}
      </div>
    </div>
  )
}

function DataPrepStep({ config, onChange }: {
  config: BacktestConfig
  onChange: (config: BacktestConfig) => void
}) {
  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h3 className="text-sm font-semibold text-text-primary uppercase tracking-wider mb-4">
          Data Configuration
        </h3>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-2">
                Start Date
              </label>
              <Input
                type="date"
                value={config.startDate}
                onChange={(e) => onChange({ ...config, startDate: e.target.value })}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-2">
                End Date
              </label>
              <Input
                type="date"
                value={config.endDate}
                onChange={(e) => onChange({ ...config, endDate: e.target.value })}
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-text-secondary mb-2">
              Frequency
            </label>
            <div className="grid grid-cols-3 gap-2">
              {(['1d', '1h', '15m'] as const).map(freq => (
                <button
                  key={freq}
                  className={cn(
                    'px-4 py-2 text-sm border rounded transition-colors',
                    config.frequency === freq
                      ? 'border-accent-primary bg-accent-primary/10 text-accent-primary'
                      : 'border-border-primary text-text-secondary hover:bg-bg-tertiary'
                  )}
                  onClick={() => onChange({ ...config, frequency: freq })}
                >
                  {freq === '1d' && 'Daily'}
                  {freq === '1h' && 'Hourly'}
                  {freq === '15m' && '15 Minutes'}
                </button>
              ))}
            </div>
          </div>

          <div className="bg-bg-tertiary border border-border-primary p-4 rounded">
            <div className="text-xs font-medium text-text-secondary uppercase tracking-wider mb-3">
              Selected Symbols
            </div>
            <div className="flex flex-wrap gap-2">
              {config.symbols.map(symbol => (
                <span key={symbol} className="px-2 py-1 bg-bg-elevated border border-border-primary rounded text-sm font-mono">
                  {symbol}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function StrategySelectStep({ config, onChange }: {
  config: BacktestConfig
  onChange: (config: BacktestConfig) => void
}) {
  const strategies = [
    { id: 'ma_cross', name: 'Moving Average Crossover', description: 'Classic trend following strategy' },
    { id: 'rsi', name: 'RSI Mean Reversion', description: 'Oversold/overbought signals' },
    { id: 'bollinger', name: 'Bollinger Bands', description: 'Volatility-based breakout' },
    { id: 'macd', name: 'MACD Momentum', description: 'Momentum trading signals' },
  ]

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h3 className="text-sm font-semibold text-text-primary uppercase tracking-wider mb-4">
          Strategy Selection
        </h3>

        <div className="space-y-2">
          {strategies.map(strategy => (
            <div
              key={strategy.id}
              className={cn(
                'p-4 border rounded cursor-pointer transition-colors',
                config.strategy === strategy.id
                  ? 'border-accent-primary bg-accent-primary/5'
                  : 'border-border-primary hover:bg-bg-tertiary'
              )}
              onClick={() => onChange({ ...config, strategy: strategy.id })}
            >
              <div className="flex items-start justify-between">
                <div>
                  <div className="text-sm font-medium text-text-primary">{strategy.name}</div>
                  <div className="text-xs text-text-tertiary mt-1">{strategy.description}</div>
                </div>
                {config.strategy === strategy.id && (
                  <div className="w-5 h-5 rounded-full bg-accent-primary flex items-center justify-center">
                    <div className="w-2 h-2 rounded-full bg-white" />
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function ParameterConfigStep({ config, onChange }: {
  config: BacktestConfig
  onChange: (config: BacktestConfig) => void
}) {
  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h3 className="text-sm font-semibold text-text-primary uppercase tracking-wider mb-4">
          Strategy Parameters
        </h3>

        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-2">
                Short MA Period
              </label>
              <Input type="number" defaultValue="20" />
            </div>
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-2">
                Long MA Period
              </label>
              <Input type="number" defaultValue="50" />
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-text-secondary mb-2">
              Position Sizing Method
            </label>
            <select className="w-full h-9 px-3 bg-bg-secondary border border-border-primary text-sm text-text-primary rounded">
              <option>Equal Weight</option>
              <option>Risk Parity</option>
              <option>Kelly Criterion</option>
            </select>
          </div>
        </div>
      </div>
    </div>
  )
}

function RiskControlStep({ config, onChange }: {
  config: BacktestConfig
  onChange: (config: BacktestConfig) => void
}) {
  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h3 className="text-sm font-semibold text-text-primary uppercase tracking-wider mb-4">
          Risk Control Settings
        </h3>

        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-text-secondary mb-2">
              Initial Cash ($)
            </label>
            <Input
              type="number"
              value={config.initialCash}
              onChange={(e) => onChange({ ...config, initialCash: Number(e.target.value) })}
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-2">
                Commission (%)
              </label>
              <Input
                type="number"
                step="0.01"
                value={config.commission}
                onChange={(e) => onChange({ ...config, commission: Number(e.target.value) })}
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-text-secondary mb-2">
                Slippage (%)
              </label>
              <Input
                type="number"
                step="0.01"
                value={config.slippage}
                onChange={(e) => onChange({ ...config, slippage: Number(e.target.value) })}
              />
            </div>
          </div>

          <div className="bg-info/10 border border-info/20 p-4 rounded">
            <div className="text-sm text-text-primary font-medium mb-2">Configuration Summary</div>
            <div className="space-y-1 text-xs text-text-secondary font-mono">
              <div>Symbols: {config.symbols.join(', ')}</div>
              <div>Period: {config.startDate} to {config.endDate}</div>
              <div>Initial Capital: ${config.initialCash.toLocaleString()}</div>
              <div>Commission: {config.commission}% | Slippage: {config.slippage}%</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
