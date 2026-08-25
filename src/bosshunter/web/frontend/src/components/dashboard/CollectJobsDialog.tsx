import { useEffect, useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import type { WorkbenchTask } from '@/hooks/useDashboard'

type PlatformId = 'boss' | 'zhilian' | '51job'

interface PlatformDraft {
  enabled: boolean
  keywords: string
  cities: string
  cityCodes: string
  maxPages: string
  sort: string
}

interface PlatformCityOption {
  name: string
  code: string
}

interface CollectJobsDialogProps {
  open: boolean
  mode?: 'collect' | 'full'
  activeTask: WorkbenchTask | null
  onClose: () => void
  onStart: (options: Record<string, unknown>) => void
}

const initialDrafts: Record<PlatformId, PlatformDraft> = {
  boss: { enabled: true, keywords: '', cities: '', cityCodes: '', maxPages: '3', sort: 'default' },
  zhilian: { enabled: false, keywords: '', cities: '', cityCodes: '', maxPages: '1', sort: 'default' },
  '51job': { enabled: false, keywords: '', cities: '上海', cityCodes: '上海=020000', maxPages: '1', sort: 'default' },
}

function splitValues(value: string) {
  return value.split(/[,，\n]/).map(item => item.trim()).filter(Boolean)
}

function parseCityCodes(value: string) {
  const result: Record<string, string> = {}
  value.split(/[,，\n]/).forEach(item => {
    const [city, code] = item.split(/[=:：]/).map(part => part.trim())
    if (city && code) result[city] = code
  })
  return result
}

function normalizeCityName(value: string) {
  const city = value.trim()
  return city.replace(/市$/, '')
}

function findPlatformCity(city: string, options: PlatformCityOption[]) {
  const target = normalizeCityName(city)
  return options.find(option => normalizeCityName(option.name) === target)
}

function draftFromConfig(config: Record<string, any> | null, platform: PlatformId): PlatformDraft {
  const legacy = platform === 'boss' && config?.search && typeof config.search === 'object' ? config.search : {}
  const specific = config?.platforms?.[platform]?.search && typeof config.platforms[platform].search === 'object'
    ? config.platforms[platform].search
    : {}
  const configured = {
    ...legacy,
    ...specific,
    keywords: specific.keywords?.length ? specific.keywords : legacy.keywords,
    cities: specific.cities?.length ? specific.cities : legacy.cities,
    city_codes: Object.keys(specific.city_codes || {}).length ? specific.city_codes : legacy.city_codes,
    max_pages: specific.max_pages || legacy.max_pages,
    sort: specific.sort || legacy.sort,
  }
  const cities = Array.isArray(configured.cities) && configured.cities.length
    ? configured.cities
    : platform === 'boss' ? (config?.profile?.target_cities || []) : []
  return {
    enabled: config?.platforms?.[platform]?.enabled ?? platform === 'boss',
    keywords: Array.isArray(configured.keywords) ? configured.keywords.join(', ') : '',
    cities: cities.join(', '),
    cityCodes: configured.city_codes && typeof configured.city_codes === 'object'
      ? Object.entries(configured.city_codes).map(([city, code]) => `${city}=${code}`).join(', ')
      : '',
    maxPages: String(configured.max_pages || (platform === 'boss' ? 3 : 1)),
    sort: configured.sort || (platform === 'boss' ? 'default' : 'default'),
  }
}

export function CollectJobsDialog({ open, mode = 'collect', activeTask, onClose, onStart }: CollectJobsDialogProps) {
  const [drafts, setDrafts] = useState(initialDrafts)
  const [order, setOrder] = useState<PlatformId[]>(['boss'])
  const [autoScore, setAutoScore] = useState(false)
  const [error, setError] = useState('')
  const [zhilianCities, setZhilianCities] = useState<PlatformCityOption[]>([])
  const [job51Cities, setJob51Cities] = useState<PlatformCityOption[]>([])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    fetch('/api/config', { cache: 'no-store' })
      .then(response => response.json())
      .then(config => {
        if (cancelled) return
        const nextBoss = draftFromConfig(config, 'boss')
        const nextZhilian = draftFromConfig(config, 'zhilian')
        const nextJob51 = draftFromConfig(config, '51job')
        if (mode === 'full') {
          nextZhilian.enabled = false
          nextJob51.enabled = false
        }
        const configuredOrder = Array.isArray(config?.collection?.default_order) ? config.collection.default_order : ['boss']
        const enabledFromConfig = (['boss', 'zhilian', '51job'] as PlatformId[]).filter(platform => config?.platforms?.[platform]?.enabled === true)
        const nextOrder = [...configuredOrder, ...enabledFromConfig].filter((item: unknown, index, values): item is PlatformId =>
          (item === 'boss' || item === 'zhilian' || item === '51job') && values.indexOf(item) === index,
        )
        setDrafts({ boss: nextBoss, zhilian: nextZhilian, '51job': nextJob51 })
        setOrder(mode === 'full' ? ['boss'] : (nextOrder.length ? nextOrder : ['boss']))
        setAutoScore(mode === 'full' || config?.collection?.auto_score_default === true)
      })
      .catch(() => {
        if (!cancelled) setError('读取采集默认配置失败，可直接填写后启动。')
      })
    fetch('/api/cities?platform=zhilian', { cache: 'no-store' })
      .then(response => response.json())
      .then(data => {
        if (cancelled) return
        if (Array.isArray(data.cities)) setZhilianCities(data.cities)
        if (!data.ok && !cancelled) setError(data.error || '读取内置智联城市目录失败。')
      })
      .catch(() => {
        if (!cancelled) setError('读取内置智联城市目录失败，可稍后重试。')
      })
    fetch('/api/cities?platform=51job', { cache: 'no-store' })
      .then(response => response.json())
      .then(data => {
        if (cancelled) return
        if (Array.isArray(data.cities)) setJob51Cities(data.cities)
        if (!data.ok) setError(data.error || '读取 51job 城市目录失败。')
      })
      .catch(() => {
        if (!cancelled) setError('读取 51job 城市目录失败，可稍后重试。')
      })
    return () => { cancelled = true }
  }, [open, mode])

  const enabledOrder = useMemo(
    () => order.filter(platform => drafts[platform].enabled),
    [drafts, order],
  )

  if (!open) return null

  const updateDraft = (platform: PlatformId, key: keyof PlatformDraft, value: string | boolean) => {
    setDrafts(previous => ({ ...previous, [platform]: { ...previous[platform], [key]: value } }))
    setError('')
  }

  const togglePlatform = (platform: PlatformId, enabled: boolean) => {
    updateDraft(platform, 'enabled', enabled)
    setOrder(previous => enabled
      ? [...previous.filter(item => item !== platform), platform]
      : previous.filter(item => item !== platform))
  }

  const move = (platform: PlatformId, direction: -1 | 1) => {
    setOrder(previous => {
      const index = previous.indexOf(platform)
      const target = index + direction
      if (index < 0 || target < 0 || target >= previous.length) return previous
      const next = [...previous]
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
  }

  const start = () => {
    if (!enabledOrder.length) {
      setError('至少勾选一个平台。')
      return
    }
    const platforms: Record<string, unknown> = {}
    for (const platform of enabledOrder) {
      const draft = drafts[platform]
      const keywords = splitValues(draft.keywords)
      const cities = splitValues(draft.cities)
      if (!keywords.length || !cities.length) {
        const label = platform === 'boss' ? 'BOSS 直聘' : platform === 'zhilian' ? '智联招聘' : '前程无忧'
        setError(`${label} 需要至少一个关键词和城市。`)
        return
      }
      const configuredCodes = parseCityCodes(draft.cityCodes)
      const platformCities = platform === 'zhilian' ? zhilianCities : platform === '51job' ? job51Cities : []
      const cityCodes = platform !== 'boss'
        ? Object.fromEntries(cities.map(city => [city, findPlatformCity(city, platformCities)?.code || configuredCodes[city] || '']).filter(([, code]) => code))
        : configuredCodes
      if (platform !== 'boss' && cities.some(city => !cityCodes[city])) {
        const missing = cities.filter(city => !cityCodes[city]).join('、')
        setError(`${platform === 'zhilian' ? '智联' : '51job'} 内置城市目录暂未收录：${missing}。请选择已验证城市。`)
        return
      }
      platforms[platform] = {
        keywords,
        cities,
        city_codes: cityCodes,
        max_pages: Number(draft.maxPages),
        sort: draft.sort,
      }
    }
    onStart({ platform_order: enabledOrder, auto_score: mode === 'full' ? true : autoScore, platforms })
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-4" role="dialog" aria-modal="true" aria-label="岗位采集">
      <div className="max-h-[92vh] w-full max-w-4xl overflow-y-auto rounded-3xl border border-card-border bg-white p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs font-black tracking-[0.18em] text-primary">COLLECT JOBS</div>
            <h2 className="mt-1 text-2xl font-black">{mode === 'full' ? '全流程采集设置' : '岗位采集'}</h2>
            <p className="mt-1 text-sm leading-6 text-muted">平台会按队列严格串行执行；每个平台只设置最大页数和排序。</p>
          </div>
          <Button variant="ghost" size="sm" onClick={onClose} aria-label="关闭"><X className="h-5 w-5" /></Button>
        </div>

        {activeTask?.progress?.platforms && (
          <div className="mt-4 rounded-2xl border border-primary/20 bg-[#FFF0E5] p-4">
            <div className="text-sm font-black text-primary">采集进行中</div>
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              {Object.entries(activeTask.progress.platforms).map(([platform, state]) => (
                <div key={platform} className="rounded-xl border border-card-border bg-white p-3 text-sm">
                  <div className="flex items-center justify-between font-black"><span>{platform === 'boss' ? 'BOSS 直聘' : platform === 'zhilian' ? '智联招聘' : '前程无忧'}</span><span>新增 {state.new}</span></div>
                  <div className="mt-1 text-xs text-muted">{state.status} · {state.city || '等待'} · {state.keyword || ''} · 第 {state.page || 0}/{state.max_pages || 0} 页</div>
                  <div className="mt-1 text-xs text-muted">扫描 {state.seen || 0} · 重复 {state.duplicate || 0} · 过滤 {state.filtered || 0} · 解析失败 {state.parse_failed || 0} · 保存失败 {state.save_failed || 0}</div>
                  {state.message && <div className="mt-1 text-xs text-primary">{state.message}</div>}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="mt-5 grid gap-4 lg:grid-cols-2">
          {(['boss', 'zhilian', '51job'] as PlatformId[]).map(platform => {
            const draft = drafts[platform]
            const label = platform === 'boss' ? 'BOSS 直聘' : platform === 'zhilian' ? '智联招聘' : '前程无忧'
            const platformCities = platform === 'zhilian' ? zhilianCities : job51Cities
            return (
              <section key={platform} className={`rounded-2xl border p-4 ${draft.enabled ? 'border-primary/30 bg-[#FFFCFA]' : 'border-card-border bg-white opacity-70'}`}>
                <div className="flex items-center justify-between gap-3">
                  <label className="flex items-center gap-2 text-lg font-black"><input type="checkbox" checked={draft.enabled} disabled={mode === 'full' && platform !== 'boss'} onChange={event => togglePlatform(platform, event.target.checked)} className="h-4 w-4 accent-primary" />{label}</label>
                  {draft.enabled && <div className="text-xs font-bold text-primary">队列 {enabledOrder.indexOf(platform) + 1}</div>}
                </div>
                {draft.enabled && <div className="mt-4 space-y-3">
                  <label className="block text-xs font-bold text-muted">关键词（逗号或换行分隔）<Input value={draft.keywords} onChange={event => updateDraft(platform, 'keywords', event.target.value)} placeholder="AI 产品经理, 产品运营" /></label>
                  <label className="block text-xs font-bold text-muted">城市（逗号或换行分隔）<Input list={platform !== 'boss' ? `${platform}-city-options` : undefined} value={draft.cities} onChange={event => updateDraft(platform, 'cities', event.target.value)} placeholder={platform === '51job' ? '上海' : '北京'} /></label>
                  {platform !== 'boss' ? <>
                    <datalist id={`${platform}-city-options`}>{platformCities.map(city => <option key={city.code} value={city.name} />)}</datalist>
                    <div className="rounded-xl border border-card-border bg-white px-3 py-2 text-xs text-muted">
                      <div className="font-bold text-foreground">平台城市编码</div>
                      <p className="mt-1">系统只使用已验证的{platform === 'zhilian' ? '智联' : '51job'}城市编码，不会猜测。</p>
                      {!!splitValues(draft.cities).length && <div className="mt-2 flex flex-wrap gap-1">
                        {splitValues(draft.cities).map(city => <span key={city} className={`rounded-full px-2 py-1 ${findPlatformCity(city, platformCities) ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>
                          {city} · {findPlatformCity(city, platformCities) ? '已自动识别' : '暂未收录'}
                        </span>)}
                      </div>}
                    </div>
                  </> : <p className="rounded-xl border border-card-border bg-white px-3 py-2 text-xs text-muted">BOSS 城市编码由系统内置匹配，无需填写。</p>}
                  <div className="grid grid-cols-2 gap-2">
                    <label className="text-xs font-bold text-muted">最大页数<Input type="number" min={1} max={10} value={draft.maxPages} onChange={event => updateDraft(platform, 'maxPages', event.target.value)} /></label>
                    <label className="text-xs font-bold text-muted">排序<Select value={draft.sort} onChange={event => updateDraft(platform, 'sort', event.target.value)}><option value="default">默认</option>{platform !== '51job' && <option value="newest">最新</option>}</Select></label>
                  </div>
                </div>}
                {!draft.enabled && mode === 'full' && platform !== 'boss' && <p className="mt-3 text-xs text-muted">当前只支持“岗位采集”，不进入发送全流程。</p>}
              </section>
            )
          })}
        </div>

        <div className="mt-4 rounded-2xl border border-card-border bg-[#FFFCFA] p-4">
          <div className="flex items-center justify-between gap-3"><div><div className="text-sm font-black">执行顺序</div><p className="mt-1 text-xs text-muted">平台串行采集；智联和前程无忧暂不执行发送或监听。</p></div><div className="flex gap-2">{enabledOrder.map((platform, index) => <div key={platform} className="flex items-center gap-1 rounded-full bg-white px-3 py-1 text-xs font-black text-primary"><span>{index + 1}. {platform === 'boss' ? 'BOSS' : platform === 'zhilian' ? '智联' : '51job'}</span><button type="button" onClick={() => move(platform, -1)} disabled={index === 0} aria-label="上移"><ArrowUp className="h-3 w-3" /></button><button type="button" onClick={() => move(platform, 1)} disabled={index === enabledOrder.length - 1} aria-label="下移"><ArrowDown className="h-3 w-3" /></button></div>)}</div></div>
        </div>

        <label className="mt-4 flex items-center justify-between rounded-2xl border border-card-border bg-white p-4"><div><div className="text-sm font-black">{mode === 'full' ? '全流程自动评分' : '采集后自动评分'}</div><p className="mt-1 text-xs leading-5 text-muted">{mode === 'full' ? '全流程必须先评分；评分后进入人工确认，再按平台适配器执行招呼和监测。' : '默认关闭；开启后只评分本轮新增岗位，评分结束即停止，不发送消息、不投递、不监测。'}</p></div><Switch checked={mode === 'full' || autoScore} onChange={mode === 'full' ? () => undefined : setAutoScore} disabled={mode === 'full'} /></label>
        {error && <div className="mt-3 rounded-xl bg-red-50 px-3 py-2 text-sm text-danger">{error}</div>}
        <div className="mt-5 flex justify-end gap-2"><Button variant="secondary" onClick={onClose}>取消</Button><Button onClick={start} disabled={Boolean(activeTask)}>{mode === 'full' ? '开始全流程' : '开始采集'}</Button></div>
      </div>
    </div>
  )
}
