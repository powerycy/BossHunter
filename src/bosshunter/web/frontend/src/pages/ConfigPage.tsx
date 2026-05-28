import { useConfig } from '@/hooks/useConfig'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Slider } from '@/components/ui/slider'
import { TagsInput } from '@/components/ui/tags-input'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Save, RotateCcw, Upload, Trash2, ChevronDown, ChevronRight } from 'lucide-react'
import { useState, useEffect } from 'react'

// City options
const CITIES = [
  '北京', '上海', '深圳', '广州', '杭州', '成都', '武汉', '南京',
  '西安', '苏州', '天津', '重庆', '郑州', '长沙', '东莞', '佛山',
  '合肥', '厦门', '青岛', '大连'
]

export default function ConfigPage() {
  const { config, schema, loading, saving, dirty, message, updateConfig, saveConfig, resetConfig } = useConfig()
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({ profile: true, search: true })
  const [resumeInfo, setResumeInfo] = useState<any>(null)

  useEffect(() => {
    fetch('/api/resume').then(r => r.json()).then(setResumeInfo).catch(() => {})
  }, [])

  const toggleSection = (key: string) => {
    setExpandedSections(prev => ({ ...prev, [key]: !prev[key] }))
  }

  const handleResumeUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const form = new FormData()
    form.append('file', file)
    const res = await fetch('/api/resume/upload', { method: 'POST', body: form })
    const data = await res.json()
    if (data.success) {
      setResumeInfo({ filename: data.filename, size: data.size, path: data.path })
      updateConfig('profile.resume_path', data.path)
    }
  }

  const handleResumeDelete = async () => {
    await fetch('/api/resume', { method: 'DELETE' })
    setResumeInfo(null)
    updateConfig('profile.resume_path', '')
  }

  if (loading || !config) {
    return <div className="flex items-center justify-center h-full text-zinc-400 text-sm">加载中...</div>
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
              <label className="block text-xs text-zinc-400 mb-2">简历文件</label>
              {resumeInfo ? (
                <div className="flex items-center gap-3 p-3 bg-zinc-800 rounded-md border border-zinc-700">
                  <span className="text-sm text-zinc-200">📄 {resumeInfo.filename}</span>
                  <span className="text-xs text-zinc-500">({(resumeInfo.size / 1024).toFixed(1)} KB)</span>
                  <button onClick={handleResumeDelete} className="ml-auto text-red-400 hover:text-red-300">
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              ) : (
                <label className="flex flex-col items-center justify-center p-6 border-2 border-dashed border-zinc-700 rounded-lg cursor-pointer hover:border-zinc-500 transition-colors">
                  <Upload className="w-6 h-6 text-zinc-500 mb-2" />
                  <span className="text-sm text-zinc-400">拖拽或点击上传 (.md)</span>
                  <input type="file" accept=".md" onChange={handleResumeUpload} className="hidden" />
                </label>
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
          </div>
        </SectionCard>

        {/* Search Section */}
        <SectionCard title="搜索设置" sectionKey="search" expanded={expandedSections} toggle={toggleSection}>
          <div className="space-y-4">
            <Field label="搜索关键词">
              <TagsInput value={config.search?.keywords || []} onChange={v => updateConfig('search.keywords', v)} />
            </Field>
            <Field label="城市">
              <div className="flex flex-wrap gap-2">
                {CITIES.map(city => {
                  const cities = (config.search?.cities?.length ? config.search.cities : config.profile?.target_cities) || []
                  const selected = cities.includes(city)
                  return (
                    <button
                      key={city}
                      type="button"
                      onClick={() => {
                        const newCities = selected ? cities.filter((c: string) => c !== city) : [...cities, city]
                        updateConfig('search.cities', newCities)
                        updateConfig('profile.target_cities', newCities)
                      }}
                      className={`px-2 py-1 text-xs rounded border transition-colors ${selected ? 'bg-primary/20 border-primary/50 text-primary' : 'bg-zinc-800 border-zinc-700 text-zinc-400 hover:border-zinc-500'}`}
                    >
                      {city}
                    </button>
                  )
                })}
              </div>
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="薪资范围">
                <Input value={config.search?.salary_range || ''} onChange={e => updateConfig('search.salary_range', e.target.value)} placeholder="如 15-25K" />
              </Field>
              <Field label="每关键词翻页数">
                <Input type="number" value={config.search?.max_pages || 3} onChange={e => updateConfig('search.max_pages', Number(e.target.value))} min={1} max={10} />
              </Field>
            </div>
          </div>
        </SectionCard>

        {/* Scoring Section */}
        <SectionCard title="评分设置" sectionKey="scoring" expanded={expandedSections} toggle={toggleSection}>
          <div className="space-y-4">
            <Field label={`通过阈值: ${config.scoring?.threshold || 60}`}>
              <Slider value={config.scoring?.threshold || 60} onChange={v => updateConfig('scoring.threshold', v)} min={0} max={100} />
            </Field>
            <Field label={`预筛阈值: ${config.scoring?.prefilter_threshold || 40}`}>
              <Slider value={config.scoring?.prefilter_threshold || 40} onChange={v => updateConfig('scoring.prefilter_threshold', v)} min={0} max={100} />
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
              <label className="text-xs text-zinc-400">发送前模拟浏览</label>
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
              <Select value={config.ai?.provider || 'anthropic'} onChange={e => updateConfig('ai.provider', e.target.value)}>
                <option value="anthropic">Anthropic</option>
                <option value="openai">OpenAI</option>
                <option value="custom">Custom</option>
              </Select>
            </Field>
            <Field label="模型名称">
              <Input value={config.ai?.model || ''} onChange={e => updateConfig('ai.model', e.target.value)} />
            </Field>
            <Field label="API Key">
              <Input type="password" value={config.ai?.api_key || ''} onChange={e => updateConfig('ai.api_key', e.target.value)} placeholder="也可通过环境变量设置" />
            </Field>
            <Field label="Base URL">
              <Input value={config.ai?.base_url || ''} onChange={e => updateConfig('ai.base_url', e.target.value)} placeholder="留空使用默认" />
            </Field>
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
          </div>
        </SectionCard>

        {/* Follow-up Section */}
        <SectionCard title="跟进设置" sectionKey="follow_up" expanded={expandedSections} toggle={toggleSection}>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <label className="text-xs text-zinc-400">启用自动跟进</label>
              <Switch checked={config.follow_up?.enabled ?? true} onChange={v => updateConfig('follow_up.enabled', v)} />
            </div>
            <Field label="跟进间隔 (小时)">
              <Input type="number" value={config.follow_up?.interval_hours || 48} onChange={e => updateConfig('follow_up.interval_hours', Number(e.target.value))} min={12} max={168} />
            </Field>
            <div className="flex items-center justify-between">
              <label className="text-xs text-zinc-400">跳过周末节假日</label>
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
        className="w-full flex items-center justify-between p-4 hover:bg-zinc-800/30 transition-colors"
        onClick={() => toggle(sectionKey)}
      >
        <span className="text-sm font-medium text-zinc-200">{title}</span>
        {isExpanded ? <ChevronDown className="w-4 h-4 text-zinc-400" /> : <ChevronRight className="w-4 h-4 text-zinc-400" />}
      </button>
      {isExpanded && <div className="px-4 pb-4">{children}</div>}
    </Card>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs text-zinc-400 mb-1.5">{label}</label>
      {children}
    </div>
  )
}
