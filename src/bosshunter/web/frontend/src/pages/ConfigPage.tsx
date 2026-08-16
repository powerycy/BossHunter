import { useConfig } from '@/hooks/useConfig'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Slider } from '@/components/ui/slider'
import { TagsInput } from '@/components/ui/tags-input'
import { CityMultiSelect, type CityOption } from '@/components/config/CityMultiSelect'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Save, RotateCcw, Upload, Trash2, ChevronDown, ChevronRight } from 'lucide-react'
import { useState, useEffect } from 'react'

const AI_SERVICES = {
  anthropic: {
    label: 'Claude / Anthropic',
    provider: 'anthropic',
    baseUrl: '',
    defaultModel: 'claude-sonnet-4-6',
    keyEnv: 'ANTHROPIC_API_KEY',
  },
  deepseek: {
    label: 'DeepSeek',
    provider: 'openai_compatible',
    baseUrl: 'https://api.deepseek.com',
    defaultModel: '',
    keyEnv: 'DEEPSEEK_API_KEY',
  },
  doubao: {
    label: '豆包 / 火山方舟',
    provider: 'openai_compatible',
    baseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
    defaultModel: '',
    keyEnv: 'ARK_API_KEY',
  },
  custom: {
    label: '其他 OpenAI 兼容接口',
    provider: 'openai_compatible',
    baseUrl: '',
    defaultModel: '',
    keyEnv: 'OPENAI_API_KEY',
  },
} as const

type AiService = keyof typeof AI_SERVICES
type PlatformId = 'boss' | 'zhilian'

export default function ConfigPage() {
  const { config, schema, loading, saving, dirty, error, message, updateConfig, saveConfig, resetConfig } = useConfig()
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({ profile: true, search: true })
  const [resumeInfo, setResumeInfo] = useState<any>(null)
  const [resumeUploadError, setResumeUploadError] = useState('')
  const [aiTest, setAiTest] = useState<{ testing: boolean; ok?: boolean; message?: string }>({ testing: false })
  const [cityOptions, setCityOptions] = useState<CityOption[]>([])
  const [zhilianCityOptions, setZhilianCityOptions] = useState<CityOption[]>([])
  const [cityRefreshing, setCityRefreshing] = useState(false)
  const [cityMessage, setCityMessage] = useState('')

  useEffect(() => {
    fetch('/api/resume').then(r => r.json()).then(setResumeInfo).catch(() => {})
    fetch('/api/cities', { cache: 'no-store' })
      .then(r => r.json())
      .then(data => {
        if (Array.isArray(data.cities)) setCityOptions(data.cities)
        if (!data.ok) setCityMessage(data.error || '本地城市列表读取失败')
      })
      .catch(() => setCityMessage('本地城市列表读取失败'))
    fetch('/api/cities?platform=zhilian', { cache: 'no-store' })
      .then(r => r.json())
      .then(data => {
        if (Array.isArray(data.cities)) setZhilianCityOptions(data.cities)
      })
      .catch(() => {})
  }, [])

  const toggleSection = (key: string) => {
    setExpandedSections(prev => ({ ...prev, [key]: !prev[key] }))
  }

  const handleResumeUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setResumeUploadError('')
    const form = new FormData()
    form.append('file', file)
    try {
      const res = await fetch('/api/resume/upload', { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok || !data.success) {
        setResumeUploadError(data.error || '简历上传失败')
        return
      }
      setResumeInfo({ filename: data.filename, size: data.size, path: data.path })
      updateConfig('profile.resume_path', data.path)
    } catch {
      setResumeUploadError('网络错误，简历上传失败')
    } finally {
      e.target.value = ''
    }
  }

  const handleResumeDelete = async () => {
    await fetch('/api/resume', { method: 'DELETE' })
    setResumeInfo(null)
    updateConfig('profile.resume_path', '')
  }

  const handleAiTest = async () => {
    if (dirty) {
      setAiTest({ testing: false, ok: false, message: '请先保存当前配置，再测试 AI 连接。' })
      return
    }
    setAiTest({ testing: true })
    try {
      const res = await fetch('/api/diagnostics/ai', { cache: 'no-store' })
      const data = await res.json()
      const check = Array.isArray(data.checks) ? data.checks[0] : null
      setAiTest({
        testing: false,
        ok: Boolean(res.ok && data.ok),
        message: check ? `${check.message}：${check.detail}` : (data.messages?.[0] || 'AI 接口未返回检测结果'),
      })
    } catch {
      setAiTest({ testing: false, ok: false, message: '无法连接本地检测接口，请确认 BossHunter 后端正在运行。' })
    }
  }

  const handleAiServiceChange = (service: AiService) => {
    const currentService = (config?.ai?.service || (config?.ai?.provider === 'openai_compatible' ? 'custom' : 'anthropic')) as AiService
    if (service === currentService) return
    if (
      (config?.ai?.api_key || config?.ai?.api_key_masked || config?.ai?.auth_token_masked)
      && !window.confirm('切换 AI 服务商会清除当前保存的 AI 凭证，是否继续？')
    ) {
      return
    }
    const preset = AI_SERVICES[service]
    updateConfig('ai.service', service)
    updateConfig('ai.provider', preset.provider)
    updateConfig('ai.base_url', preset.baseUrl)
    updateConfig('ai.model', preset.defaultModel)
    updateConfig('ai.api_key', '')
    updateConfig('ai.api_key_masked', '')
    updateConfig('ai.auth_token_masked', '')
    updateConfig('ai.clear_credentials', true)
    setAiTest({ testing: false })
  }

  const handleCityRefresh = async () => {
    setCityRefreshing(true)
    setCityMessage('')
    try {
      const res = await fetch('/api/cities/refresh', { method: 'POST' })
      const data = await res.json()
      if (Array.isArray(data.cities)) setCityOptions(data.cities)
      if (!res.ok || !data.ok) throw new Error(data.error || '刷新失败，继续使用本地城市列表')
      setCityMessage(`已刷新 ${data.count} 个城市。`)
    } catch (error) {
      setCityMessage(error instanceof Error ? error.message : '刷新失败，继续使用本地城市列表')
    } finally {
      setCityRefreshing(false)
    }
  }

  const platformSearch = (platform: PlatformId) => {
    const legacy = platform === 'boss' && config?.search && typeof config.search === 'object' ? config.search : {}
    const specific = config?.platforms?.[platform]?.search && typeof config.platforms[platform].search === 'object'
      ? config.platforms[platform].search
      : {}
    return {
      ...legacy,
      ...specific,
      keywords: specific.keywords?.length ? specific.keywords : legacy.keywords,
      cities: specific.cities?.length ? specific.cities : legacy.cities,
      city_codes: Object.keys(specific.city_codes || {}).length ? specific.city_codes : legacy.city_codes,
      max_pages: specific.max_pages || legacy.max_pages || 3,
      sort: specific.sort || legacy.sort || 'default',
      target_count: specific.target_count ?? legacy.target_count ?? 10,
    }
  }

  const updatePlatformSearch = (platform: PlatformId, key: string, value: any) => {
    updateConfig(`platforms.${platform}.search.${key}`, value)
    if (platform === 'boss') updateConfig(`search.${key}`, value)
  }

  const updatePlatformCities = (platform: PlatformId, cities: string[]) => {
    const cityCodes = platform === 'zhilian'
      ? Object.fromEntries(cities.map(city => {
        const found = zhilianCityOptions.find(option => option.name.replace(/市$/, '') === city.replace(/市$/, ''))
        return [city, found?.code || '']
      }).filter(([, code]) => code))
      : Object.fromEntries(cityOptions.filter(city => cities.includes(city.name)).map(city => [city.name, city.code]))
    updatePlatformSearch(platform, 'cities', cities)
    updatePlatformSearch(platform, 'city_codes', cityCodes)
    if (platform === 'boss') updateConfig('profile.target_cities', cities)
  }

  const setPlatformEnabled = (platform: PlatformId, enabled: boolean) => {
    updateConfig(`platforms.${platform}.enabled`, enabled)
    const currentOrder: PlatformId[] = Array.isArray(config?.collection?.default_order)
      ? config.collection.default_order.filter((item: unknown): item is PlatformId => item === 'boss' || item === 'zhilian')
      : ['boss'] as PlatformId[]
    const nextOrder = enabled
      ? [...currentOrder, ...(!currentOrder.includes(platform) ? [platform] : [])]
      : currentOrder.filter(item => item !== platform)
    updateConfig('collection.default_order', nextOrder.length ? nextOrder : ['boss'])
  }

  const setCollectionOrder = (value: string) => {
    const enabled = (['boss', 'zhilian'] as PlatformId[]).filter(platform => config?.platforms?.[platform]?.enabled !== false)
    const requested = value.split(',').filter((item): item is PlatformId => item === 'boss' || item === 'zhilian')
    const next = [...requested, ...enabled.filter(platform => !requested.includes(platform))]
    updateConfig('collection.default_order', next.length ? next : ['boss'])
  }

  if (loading) {
    return <div className="flex items-center justify-center h-full text-muted text-sm">加载中...</div>
  }

  if (error || !config) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="max-w-md rounded-2xl border border-card-border bg-[#FFFCFA] p-6 text-center">
          <div className="text-sm font-black text-foreground">配置加载失败</div>
          <p className="mt-2 text-xs leading-6 text-muted">
            请确认后端服务已启动：在项目根目录运行 bosshunter web，或启动 127.0.0.1:8686 后刷新页面。
          </p>
          {error && <p className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-500">{error}</p>}
          <Button className="mt-4" size="sm" onClick={resetConfig}>重试</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto space-y-4 pr-4">
        {/* Actions bar */}
        <div className="flex items-center justify-between sticky top-0 bg-background z-10 py-2">
          <div className="flex items-center gap-2">
            {dirty && <span className="text-xs text-amber-400">有未保存的更改</span>}
            {message && (
              <span className={`text-xs ${message.type === 'success' ? 'text-green-400' : 'text-red-400'}`}>
                {message.text}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={resetConfig}><RotateCcw className="w-3 h-3 mr-1" />重置</Button>
            <Button size="sm" onClick={saveConfig} disabled={saving || !dirty}><Save className="w-3 h-3 mr-1" />{saving ? '保存中...' : '保存'}</Button>
          </div>
        </div>

        {/* Profile Section */}
        <SectionCard title="个人信息" sectionKey="profile" expanded={expandedSections} toggle={toggleSection}>
          <div className="space-y-4">
            {/* Resume upload */}
            <div>
              <label className="block text-xs text-foreground mb-2">简历文件</label>
              {resumeInfo ? (
                <div className="flex items-center gap-3 rounded-md border border-card-border bg-[#FFFCFA] p-3">
                  <span className="text-sm font-bold text-foreground">📄 {resumeInfo.filename}</span>
                  <span className="text-xs text-muted">({(resumeInfo.size / 1024).toFixed(1)} KB)</span>
                  <button onClick={handleResumeDelete} className="ml-auto text-red-400 hover:text-red-300">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ) : (
                <label className="flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-card-border p-6 transition-colors hover:border-primary/50 hover:bg-[#FFFCFA]">
                  <Upload className="mb-2 h-6 w-6 text-muted" />
                  <span className="text-sm text-muted">拖拽或点击上传 (.md、.docx、.pdf)</span>
                  <input type="file" accept=".md,.docx,.pdf,application/pdf" onChange={handleResumeUpload} className="hidden" />
                </label>
              )}
              {resumeUploadError && (
                <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-500">{resumeUploadError}</p>
              )}
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="最低薪资 (K)">
                <Input type="number" value={config.profile?.salary_min || 0} onChange={e => updateConfig('profile.salary_min', Number(e.target.value))} min={0} max={200} />
              </Field>
              <Field label="最高薪资 (K)">
                <Input type="number" value={config.profile?.salary_max || 0} onChange={e => updateConfig('profile.salary_max', Number(e.target.value))} min={0} max={200} />
              </Field>
            </div>
            <Field label="排除关键词">
              <TagsInput value={config.profile?.deal_breakers || []} onChange={v => updateConfig('profile.deal_breakers', v)} placeholder="如：外包、996" />
            </Field>
            <Field label="JD 排除关键词">
              <TagsInput value={config.profile?.jd_deal_breakers || []} onChange={v => updateConfig('profile.jd_deal_breakers', v)} placeholder="如：需频繁出差、纯销售" />
              <p className="mt-1 text-xs text-muted">完整 JD 含这些词时会在 AI 评分前跳过。</p>
            </Field>
            <Field label="屏蔽公司">
              <TagsInput value={config.profile?.blocked_companies || []} onChange={v => updateConfig('profile.blocked_companies', v)} placeholder="输入公司名称或关键词" />
              <p className="mt-1 text-xs text-muted">公司名包含这些词时不采集，也不会进入 AI 评分。</p>
            </Field>
            <div className="flex items-center justify-between">
              <label className="text-xs text-foreground">接受实习/管培岗位</label>
              <Switch checked={config.profile?.allow_internship ?? false} onChange={v => updateConfig('profile.allow_internship', v)} />
            </div>
          </div>
        </SectionCard>

        {/* Search Section */}
        <SectionCard title="搜索设置" sectionKey="search" expanded={expandedSections} toggle={toggleSection}>
          <div className="space-y-4">
            <p className="rounded-xl border border-card-border bg-[#FFFCFA] px-3 py-2 text-xs leading-5 text-muted">
              这里是岗位采集的全局配置。单独采集窗口和运行全流程都会读取已保存的平台设置；智联城市编码由内置目录自动匹配，不需要手工填写。
            </p>
            {(['boss', 'zhilian'] as PlatformId[]).map(platform => {
              const search = platformSearch(platform)
              const label = platform === 'boss' ? 'BOSS 直聘' : '智联招聘'
              const enabled = config.platforms?.[platform]?.enabled ?? platform === 'boss'
              const cities = Array.isArray(search.cities) && search.cities.length
                ? search.cities
                : platform === 'boss' ? (config.profile?.target_cities || []) : []
              const targetCount = search.target_count
              const cityInput = cities.join(', ')
              return (
                <div key={platform} className={`rounded-2xl border p-4 ${enabled ? 'border-primary/30 bg-[#FFFCFA]' : 'border-card-border bg-white opacity-70'}`}>
                  <div className="flex items-center justify-between gap-3">
                    <label className="flex items-center gap-2 text-sm font-black text-foreground">
                      <input type="checkbox" checked={enabled} onChange={event => setPlatformEnabled(platform, event.target.checked)} className="h-4 w-4 accent-primary" />
                      {label}
                    </label>
                    <span className="text-xs text-muted">{enabled ? '已启用' : '未启用'}</span>
                  </div>
                  {enabled && <div className="mt-4 space-y-3">
                    <Field label="搜索关键词">
                      <TagsInput value={Array.isArray(search.keywords) ? search.keywords : []} onChange={value => updatePlatformSearch(platform, 'keywords', value)} placeholder="如：人力、产品运营" />
                    </Field>
                    <Field label="搜索城市">
                      {platform === 'boss' ? <CityMultiSelect
                        options={cityOptions}
                        value={cities}
                        onChange={value => updatePlatformCities(platform, value)}
                        onRefresh={handleCityRefresh}
                        refreshing={cityRefreshing}
                        message={cityMessage}
                      /> : <>
                        <Input list="config-zhilian-city-options" value={cityInput} onChange={event => updatePlatformCities(platform, event.target.value.split(/[,，]/).map(value => value.trim()).filter(Boolean))} placeholder="如：深圳" />
                        <datalist id="config-zhilian-city-options">{zhilianCityOptions.map(city => <option key={city.code} value={city.name} />)}</datalist>
                        <p className="mt-1 text-xs text-muted">智联城市编码由系统自动匹配；当前内置 {zhilianCityOptions.length} 个城市。</p>
                        {!!cities.length && <div className="mt-2 flex flex-wrap gap-1">{cities.map((city: string) => {
                          const matched = zhilianCityOptions.find(option => option.name.replace(/市$/, '') === city.replace(/市$/, ''))
                          return <span key={city} className={`rounded-full px-2 py-1 text-xs ${matched ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>{city} · {matched ? '已自动识别' : '暂未收录'}</span>
                        })}</div>}
                      </>}
                    </Field>
                    <div className="grid gap-3 md:grid-cols-3">
                      <Field label="最大页数">
                        <Input type="number" value={search.max_pages || 3} onChange={event => updatePlatformSearch(platform, 'max_pages', Number(event.target.value))} min={1} max={10} />
                      </Field>
                      <Field label="排序">
                        <Select value={search.sort || 'default'} onChange={event => updatePlatformSearch(platform, 'sort', event.target.value)}>
                          <option value="default">默认</option>
                          <option value="newest">最新</option>
                        </Select>
                      </Field>
                      <Field label="目标新增">
                        <Input type="number" value={targetCount == null ? '' : targetCount} disabled={targetCount == null} onChange={event => updatePlatformSearch(platform, 'target_count', Number(event.target.value))} min={1} max={500} placeholder="目标数量" />
                      </Field>
                    </div>
                    <div className="flex items-center justify-between rounded-xl border border-card-border bg-white px-3 py-2 text-xs font-bold text-muted">
                      不限数量（仍受最大页数限制）
                      <Switch checked={targetCount == null} onChange={value => updatePlatformSearch(platform, 'target_count', value ? null : 10)} />
                    </div>
                  </div>}
                </div>
              )
            })}
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="默认执行顺序">
                <Select value={Array.isArray(config.collection?.default_order) ? config.collection.default_order.join(',') : 'boss'} onChange={event => setCollectionOrder(event.target.value)}>
                  <option value="boss">BOSS 直聘</option>
                  <option value="zhilian">智联招聘</option>
                  <option value="boss,zhilian">BOSS 直聘 → 智联招聘</option>
                  <option value="zhilian,boss">智联招聘 → BOSS 直聘</option>
                </Select>
              </Field>
              <div className="flex items-center justify-between rounded-xl border border-card-border bg-[#FFFCFA] px-3 py-2 text-xs font-bold text-muted">
                采集后自动评分
                <Switch checked={config.collection?.auto_score_default ?? false} onChange={value => updateConfig('collection.auto_score_default', value)} />
              </div>
            </div>
          </div>
        </SectionCard>

        {/* Scoring Section */}
        <SectionCard title="评分设置" sectionKey="scoring" expanded={expandedSections} toggle={toggleSection}>
          <div className="space-y-4">
            <Field label={`通过阈值: ${config.scoring?.threshold || 60}`}>
              <Slider value={config.scoring?.threshold || 60} onChange={v => updateConfig('scoring.threshold', v)} min={0} max={100} />
            </Field>
            <Field label="每轮最大候选数">
              <Input type="number" value={config.scoring?.max_candidates || 20} onChange={e => updateConfig('scoring.max_candidates', Number(e.target.value))} min={1} max={100} />
            </Field>
          </div>
        </SectionCard>

        {/* Throttle Section */}
        <SectionCard title="反检测设置" sectionKey="throttle" expanded={expandedSections} toggle={toggleSection}>
          <div className="space-y-4">
            <div className="grid grid-cols-3 gap-4">
              <Field label="每日发送上限">
                <Input type="number" value={config.throttle?.daily_limit || 30} onChange={e => updateConfig('throttle.daily_limit', Number(e.target.value))} />
              </Field>
              <Field label="最短间隔 (秒)">
                <Input type="number" value={config.throttle?.interval_min || 60} onChange={e => updateConfig('throttle.interval_min', Number(e.target.value))} />
              </Field>
              <Field label="最长间隔 (秒)">
                <Input type="number" value={config.throttle?.interval_max || 180} onChange={e => updateConfig('throttle.interval_max', Number(e.target.value))} />
              </Field>
            </div>
            <div className="flex items-center justify-between">
              <label className="text-xs text-foreground">发送前模拟浏览</label>
              <Switch checked={config.throttle?.browse_before_greet ?? true} onChange={v => updateConfig('throttle.browse_before_greet', v)} />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="浏览最短时长 (秒)">
                <Input type="number" value={config.throttle?.browse_duration_min || 15} onChange={e => updateConfig('throttle.browse_duration_min', Number(e.target.value))} />
              </Field>
              <Field label="浏览最长时长 (秒)">
                <Input type="number" value={config.throttle?.browse_duration_max || 30} onChange={e => updateConfig('throttle.browse_duration_max', Number(e.target.value))} />
              </Field>
            </div>
            <Field label="发送时间窗口">
              <TagsInput value={config.throttle?.send_windows || ['09:00-16:00']} onChange={v => updateConfig('throttle.send_windows', v)} placeholder="HH:MM-HH:MM" />
              <p className="mt-1 text-xs text-muted">后台任务会在当天最后一个发送窗口结束时自动停止。</p>
            </Field>
            <Field label="随机休息概率">
              <Input type="number" value={config.throttle?.day_off_probability || 0.05} onChange={e => updateConfig('throttle.day_off_probability', Number(e.target.value))} step={0.01} min={0} max={1} />
            </Field>
          </div>
        </SectionCard>

        {/* AI Section */}
        <SectionCard title="AI 设置" sectionKey="ai" expanded={expandedSections} toggle={toggleSection}>
          <div className="space-y-4">
            <Field label="提供商">
              <Select
                value={config.ai?.service || (config.ai?.provider === 'openai_compatible' ? 'custom' : 'anthropic')}
                onChange={e => handleAiServiceChange(e.target.value as AiService)}
              >
                {Object.entries(AI_SERVICES).map(([value, preset]) => (
                  <option key={value} value={value}>{preset.label}</option>
                ))}
              </Select>
              <p className="mt-1 text-xs text-muted">
                BossHunter 会自动配置协议和服务地址；也可安全复用环境变量 {
                  AI_SERVICES[(config.ai?.service || (config.ai?.provider === 'openai_compatible' ? 'custom' : 'anthropic')) as AiService].keyEnv
                }，不会在前端显示其内容。
              </p>
            </Field>
            <Field label="模型名称">
              <Input value={config.ai?.model || ''} onChange={e => {
                updateConfig('ai.model', e.target.value)
                setAiTest({ testing: false })
              }} placeholder="填写服务商当前支持的模型 ID" />
            </Field>
            <Field label="API Key">
              <Input type="password" value={config.ai?.api_key || ''} onChange={e => {
                updateConfig('ai.api_key', e.target.value)
                setAiTest({ testing: false })
              }} placeholder={config.ai?.api_key_masked || '也可通过环境变量设置'} />
            </Field>
            <Field label="Base URL">
              <Input value={config.ai?.base_url || ''} onChange={e => {
                updateConfig('ai.base_url', e.target.value)
                setAiTest({ testing: false })
              }} placeholder="留空使用默认" />
            </Field>
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Thinking 模式">
                <Select
                  value={config.ai?.thinking || 'auto'}
                  onChange={e => updateConfig('ai.thinking', e.target.value)}
                >
                  <option value="auto">自动兼容（推荐）</option>
                  <option value="disabled">强制关闭</option>
                  <option value="enabled">强制开启</option>
                  <option value="off">不发送参数</option>
                </Select>
                <p className="mt-1 text-xs text-muted">自动模式优先获取纯文本；接口不支持 thinking 参数时会安全回退。</p>
              </Field>
              <Field label="Thinking 预算 Token">
                <Input
                  type="number"
                  value={config.ai?.thinking_budget || 2048}
                  onChange={e => updateConfig('ai.thinking_budget', Number(e.target.value))}
                  min={1024}
                  max={32768}
                  disabled={(config.ai?.thinking || 'auto') !== 'enabled'}
                />
              </Field>
            </div>
            <Field label="AI 请求超时 (秒)">
              <Input
                type="number"
                value={config.ai?.timeout_seconds || 180}
                onChange={e => updateConfig('ai.timeout_seconds', Number(e.target.value))}
                min={5}
                max={600}
              />
            </Field>
            <Field label="AI 评分并发数">
              <Select
                value={String(config.ai?.scoring_concurrency || 1)}
                onChange={e => updateConfig('ai.scoring_concurrency', Number(e.target.value))}
              >
                {[1, 2, 3].map(value => <option key={value} value={value}>{value}</option>)}
              </Select>
              <p className="mt-1 text-xs text-muted">默认 1；提高并发会增加 API 限流风险。</p>
            </Field>
            <div className="flex items-center justify-between rounded-lg border border-card-border bg-[#FFFCFA] p-3">
              <div>
                <label className="text-xs font-bold text-foreground">临界评分二次复核</label>
                <p className="mt-1 text-xs text-muted">默认关闭；开启后会增加 AI 调用次数。</p>
              </div>
              <Switch checked={config.ai?.scoring_second_review ?? false} onChange={v => updateConfig('ai.scoring_second_review', v)} />
            </div>
            <div className="rounded-2xl border border-card-border bg-[#FFFCFA] p-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-black text-foreground">AI 连接检测</div>
                  <p className="mt-1 text-xs text-muted">不会消耗对话 Token；检测已保存的 Key、Base URL 和服务可用性。</p>
                </div>
                <Button variant="secondary" size="sm" onClick={handleAiTest} disabled={aiTest.testing}>
                  {aiTest.testing ? '检测中...' : '测试连接'}
                </Button>
              </div>
              {aiTest.message && (
                <p className={`mt-2 rounded-lg px-3 py-2 text-xs ${
                  aiTest.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-500'
                }`}>
                  {aiTest.message}
                </p>
              )}
            </div>
          </div>
        </SectionCard>

        {/* Monitor Section */}
        <SectionCard title="监控设置" sectionKey="monitor" expanded={expandedSections} toggle={toggleSection}>
          <div className="space-y-4">
            <Field label="检查间隔 (分钟)">
              <Input type="number" value={config.monitor?.interval || 30} onChange={e => updateConfig('monitor.interval', Number(e.target.value))} min={1} max={120} />
            </Field>
            <Field label="聊天页 URL">
              <Input value={config.monitor?.chat_url || ''} onChange={e => updateConfig('monitor.chat_url', e.target.value)} />
            </Field>
            <Field label="每轮最多发简历数">
              <Input type="number" value={config.monitor?.max_resume_sends_per_cycle || 5} onChange={e => updateConfig('monitor.max_resume_sends_per_cycle', Number(e.target.value))} min={1} />
            </Field>
            <div className="flex items-center justify-between rounded-2xl border border-card-border bg-[#FFFCFA] p-4">
              <div>
                <label className="text-sm font-black text-foreground">检测到 HR 问题时自动回复</label>
                <p className="mt-1 text-xs text-muted">默认关闭。关闭时只生成回复建议，需要你在“监测执行”中确认后发送。</p>
              </div>
              <Switch checked={config.monitor?.auto_reply_hr_questions ?? false} onChange={v => updateConfig('monitor.auto_reply_hr_questions', v)} />
            </div>
          </div>
        </SectionCard>

        {/* Follow-up Section */}
        <SectionCard title="跟进设置" sectionKey="follow_up" expanded={expandedSections} toggle={toggleSection}>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <label className="text-xs text-foreground">启用自动跟进</label>
              <Switch checked={config.follow_up?.enabled ?? false} onChange={v => updateConfig('follow_up.enabled', v)} />
            </div>
            <Field label="跟进间隔 (小时)">
              <Input type="number" value={config.follow_up?.interval_hours || 48} onChange={e => updateConfig('follow_up.interval_hours', Number(e.target.value))} min={12} max={168} />
            </Field>
            <div className="flex items-center justify-between">
              <label className="text-xs text-foreground">跳过周末节假日</label>
              <Switch checked={config.follow_up?.skip_weekends ?? true} onChange={v => updateConfig('follow_up.skip_weekends', v)} />
            </div>
          </div>
        </SectionCard>

        {/* Dedup Section */}
        <SectionCard title="去重设置" sectionKey="dedup" expanded={expandedSections} toggle={toggleSection}>
          <div className="space-y-4">
            <Field label="历史记录文件路径">
              <Input value={config.dedup?.history_file || ''} onChange={e => updateConfig('dedup.history_file', e.target.value)} />
            </Field>
          </div>
        </SectionCard>
    </div>
  )
}

// Helper components
function SectionCard({ title, sectionKey, expanded, toggle, children }: {
  title: string; sectionKey: string; expanded: Record<string, boolean>; toggle: (k: string) => void; children: React.ReactNode
}) {
  const isExpanded = expanded[sectionKey] ?? false
  return (
    <Card>
      <button
        className="w-full flex items-center justify-between p-4 transition-colors hover:bg-[#FFFCFA]"
        onClick={() => toggle(sectionKey)}
      >
        <span className="text-sm font-black text-foreground">{title}</span>
        {isExpanded ? <ChevronDown className="w-4 h-4 text-foreground" /> : <ChevronRight className="w-4 h-4 text-foreground" />}
      </button>
      {isExpanded && <div className="px-4 pb-4">{children}</div>}
    </Card>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-foreground mb-1.5">{label}</label>
      {children}
    </div>
  )
}
