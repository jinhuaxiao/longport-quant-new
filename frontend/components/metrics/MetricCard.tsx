import { cn, formatNumber, formatReturn, getReturnColor } from '@/lib/utils'

interface MetricCardProps {
  label: string
  value: string | number
  change?: number
  changeLabel?: string
  precision?: number
  format?: 'number' | 'percent' | 'currency'
  className?: string
}

export function MetricCard({
  label,
  value,
  change,
  changeLabel = 'vs Previous',
  precision = 2,
  format = 'number',
  className,
}: MetricCardProps) {
  const formatValue = (val: string | number) => {
    if (typeof val === 'string') return val

    switch (format) {
      case 'percent':
        return formatReturn(val, precision)
      case 'currency':
        return `$${formatNumber(val, precision)}`
      default:
        return formatNumber(val, precision)
    }
  }

  const formattedValue = formatValue(value)
  const changeColor = change !== undefined ? getReturnColor(change) : ''

  return (
    <div className={cn('bg-bg-tertiary border border-border-primary p-4', className)}>
      {/* Label */}
      <div className="text-xs text-text-secondary uppercase tracking-wider mb-2">
        {label}
      </div>

      {/* Value */}
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-mono font-semibold text-text-primary tabular-nums">
          {formattedValue}
        </span>

        {/* Change */}
        {change !== undefined && (
          <div className="flex items-center gap-1 text-sm">
            <span className={cn('font-mono tabular-nums', changeColor)}>
              {change > 0 ? '+' : ''}{change.toFixed(2)}%
            </span>
          </div>
        )}
      </div>

      {/* Secondary info */}
      {change !== undefined && (
        <div className="mt-1 text-xs text-text-tertiary">
          {changeLabel}
        </div>
      )}
    </div>
  )
}
