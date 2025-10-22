'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { cn } from '@/lib/utils'

const navigation = [
  { name: 'Research', href: '/research', section: 'workspace' },
  { name: 'Backtest Results', href: '/research/results', section: 'workspace' },
  { name: 'Strategy Library', href: '/research/library', section: 'workspace' },

  { name: 'Data Manager', href: '/data', section: 'data' },
  { name: 'Data Quality', href: '/data/quality', section: 'data' },

  { name: 'Model Training', href: '/ml/training', section: 'ml' },
  { name: 'Model Registry', href: '/ml/models', section: 'ml' },

  { name: 'Live Trading', href: '/live/monitor', section: 'live' },
  { name: 'Positions', href: '/live/positions', section: 'live' },
  { name: 'Order Management', href: '/live/orders', section: 'live' },

  { name: 'Performance', href: '/analysis/performance', section: 'analysis' },
  { name: 'Risk Analytics', href: '/analysis/risk', section: 'analysis' },

  { name: 'Settings', href: '/settings', section: 'settings' },
]

const sections: Record<string, string> = {
  workspace: 'Research Workspace',
  data: 'Data Management',
  ml: 'Machine Learning',
  live: 'Live Trading',
  analysis: 'Analytics',
  settings: 'Configuration',
}

export function Sidebar() {
  const pathname = usePathname()

  return (
    <aside className="w-56 bg-bg-secondary border-r border-border-primary flex flex-col">
      {/* Logo */}
      <div className="h-14 flex items-center px-4 border-b border-border-primary">
        <span className="text-base font-semibold text-text-primary">LongPort Quant</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4 overflow-y-auto">
        {Object.entries(sections).map(([key, title]) => {
          const items = navigation.filter(item => item.section === key)
          if (items.length === 0) return null

          return (
            <div key={key} className="mb-6">
              {/* Section Header */}
              <div className="px-4 mb-2">
                <h3 className="text-xs font-medium text-text-tertiary uppercase tracking-wider">
                  {title}
                </h3>
              </div>

              {/* Section Items */}
              <div className="space-y-0.5">
                {items.map(item => (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cn(
                      'block px-4 py-1.5 text-sm transition-colors',
                      'hover:bg-bg-tertiary hover:text-text-primary',
                      pathname === item.href
                        ? 'bg-bg-tertiary text-text-primary font-medium'
                        : 'text-text-secondary'
                    )}
                  >
                    {item.name}
                  </Link>
                ))}
              </div>
            </div>
          )
        })}
      </nav>

      {/* System Status */}
      <div className="border-t border-border-primary p-4">
        <div className="flex items-center justify-between text-xs">
          <span className="text-text-tertiary">System Status</span>
          <div className="flex items-center gap-1.5">
            <div className="w-1.5 h-1.5 rounded-full bg-success" />
            <span className="text-text-secondary font-mono">Operational</span>
          </div>
        </div>
        <div className="mt-2 flex items-center justify-between text-xs">
          <span className="text-text-tertiary">Market Session</span>
          <span className="text-text-secondary font-mono">Pre-Market</span>
        </div>
      </div>
    </aside>
  )
}
