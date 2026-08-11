import { useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'

export interface CityOption {
  name: string
  code: string
  first_char?: string
  hot?: boolean
}

export function CityMultiSelect({
  options,
  value,
  onChange,
  onRefresh,
  refreshing,
  message,
}: {
  options: CityOption[]
  value: string[]
  onChange: (cities: string[]) => void
  onRefresh: () => void
  refreshing: boolean
  message: string
}) {
  const [query, setQuery] = useState('')
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return options.filter(city => !needle || city.name.toLowerCase().includes(needle) || city.code.includes(needle)).slice(0, 80)
  }, [options, query])
  const toggle = (name: string) => onChange(value.includes(name) ? value.filter(item => item !== name) : [...value, name])
  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索城市名或 BOSS 编码" className="min-w-0 flex-1 rounded-xl border border-card-border bg-[#FFFCFA] px-3 py-2 text-xs outline-none focus:border-primary" />
        <Button type="button" variant="secondary" size="sm" onClick={onRefresh} disabled={refreshing}>{refreshing ? '刷新中...' : '刷新城市列表'}</Button>
      </div>
      <div className="flex max-h-44 flex-wrap gap-2 overflow-y-auto rounded-xl border border-card-border bg-[#FFFCFA] p-2">
        {visible.map(city => {
          const selected = value.includes(city.name)
          return <button key={city.code} type="button" onClick={() => toggle(city.name)} className={`rounded-lg border px-2 py-1 text-xs transition-colors ${selected ? 'border-primary/50 bg-primary/20 text-primary' : 'border-card-border bg-white text-muted hover:border-primary/40 hover:text-foreground'}`}>{city.name} · {city.code}</button>
        })}
        {!visible.length && <span className="p-2 text-xs text-muted">没有匹配的城市。</span>}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted"><span>已选 {value.length} 个</span>{value.map(name => <span key={name} className="rounded-full bg-[#FFF0E5] px-2 py-1 text-primary">{name}</span>)}</div>
      {message && <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">{message}</p>}
    </div>
  )
}
