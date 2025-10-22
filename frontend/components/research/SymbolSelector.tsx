'use client'

import { useState } from 'react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { Search, X, Plus, Folder } from 'lucide-react'

interface Portfolio {
  id: number
  name: string
  symbols: string[]
}

interface SymbolSelectorProps {
  selected: string[]
  onChange: (symbols: string[]) => void
}

export function SymbolSelector({ selected, onChange }: SymbolSelectorProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const [portfolios] = useState<Portfolio[]>([
    { id: 1, name: 'HK Tech', symbols: ['09988.HK', '00700.HK', '03690.HK'] },
    { id: 2, name: 'US Large Cap', symbols: ['AAPL', 'MSFT', 'GOOGL', 'AMZN'] },
    { id: 3, name: 'Semiconductor', symbols: ['NVDA', 'TSM', 'ASML'] },
  ])

  const handleRemove = (symbol: string) => {
    onChange(selected.filter(s => s !== symbol))
  }

  const handleAdd = (symbol: string) => {
    if (!selected.includes(symbol)) {
      onChange([...selected, symbol])
    }
  }

  const loadPortfolio = (portfolio: Portfolio) => {
    onChange([...new Set([...selected, ...portfolio.symbols])])
  }

  // Mock search results
  const searchResults = searchQuery.length > 0
    ? [
        { symbol: 'AAPL', name: 'Apple Inc.' },
        { symbol: 'TSLA', name: 'Tesla Inc.' },
        { symbol: '00700.HK', name: 'Tencent Holdings' },
      ].filter(item =>
        item.symbol.toLowerCase().includes(searchQuery.toLowerCase()) ||
        item.name.toLowerCase().includes(searchQuery.toLowerCase())
      )
    : []

  return (
    <div className="h-full flex flex-col border-r border-border-primary bg-bg-secondary">
      {/* Search Box */}
      <div className="p-4 border-b border-border-primary">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-text-tertiary" />
          <Input
            placeholder="Search symbol or name..."
            className="pl-9 bg-bg-tertiary"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        {/* Search Results Dropdown */}
        {searchResults.length > 0 && (
          <div className="mt-2 border border-border-primary bg-bg-tertiary rounded overflow-hidden">
            {searchResults.map(result => (
              <div
                key={result.symbol}
                className="px-3 py-2 hover:bg-bg-elevated cursor-pointer border-b border-border-primary last:border-0"
                onClick={() => {
                  handleAdd(result.symbol)
                  setSearchQuery('')
                }}
              >
                <div className="font-mono text-sm text-text-primary">{result.symbol}</div>
                <div className="text-xs text-text-tertiary">{result.name}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Selected Symbols */}
      <div className="flex-1 overflow-auto">
        <div className="px-4 py-2 text-xs font-medium text-text-tertiary uppercase tracking-wider">
          Selected ({selected.length})
        </div>
        <div className="px-2">
          {selected.length === 0 ? (
            <div className="px-2 py-8 text-center text-sm text-text-tertiary">
              No symbols selected
            </div>
          ) : (
            selected.map(symbol => (
              <div
                key={symbol}
                className="flex items-center justify-between p-2 rounded hover:bg-bg-tertiary group"
              >
                <div className="flex items-center gap-2">
                  <div className="h-2 w-2 rounded-full bg-success" />
                  <span className="font-mono text-sm text-text-primary">{symbol}</span>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 opacity-0 group-hover:opacity-100"
                  onClick={() => handleRemove(symbol)}
                >
                  <X className="h-3 w-3" />
                </Button>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Portfolios */}
      <div className="border-t border-border-primary p-4">
        <div className="text-xs font-medium text-text-tertiary uppercase tracking-wider mb-2">
          Portfolios
        </div>
        <div className="space-y-1">
          {portfolios.map(portfolio => (
            <div
              key={portfolio.id}
              className="flex items-center justify-between p-2 rounded hover:bg-bg-tertiary cursor-pointer group"
              onClick={() => loadPortfolio(portfolio)}
            >
              <div className="flex items-center gap-2">
                <Folder className="h-4 w-4 text-text-tertiary" />
                <div>
                  <div className="text-sm text-text-primary">{portfolio.name}</div>
                  <div className="text-xs text-text-tertiary">
                    {portfolio.symbols.length} symbols
                  </div>
                </div>
              </div>
              <div className="text-xs text-accent-primary opacity-0 group-hover:opacity-100">
                Load
              </div>
            </div>
          ))}
        </div>
        <Button variant="outline" size="sm" className="w-full mt-3 h-8">
          <Plus className="h-3 w-3 mr-1" /> New Portfolio
        </Button>
      </div>
    </div>
  )
}
