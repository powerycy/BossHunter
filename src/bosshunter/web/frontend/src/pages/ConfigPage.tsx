import { useConfig } from '@/hooks/useConfig'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Slider } from '@/components/ui/slider'
import { TagsInput } from '@/components/ui/tags-input'
import { CityMultiSelect, type CityOption } from '@/components/config/CityMultiSelect'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Save, RotateCcw, Upload, Trash2, ChevronDown, ChevronRight, ArrowRight, Bot } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useState, useEffect } from 'react'

export default function ConfigPage() {
  const { config, schema, loading, saving, dirty, error, message, updateConfig, saveConfig, resetConfig } = useConfig()
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({ profile: true, search: true })
  const [resumeInfo, setResumeInfo] = useState<any>(null)
  const [resumeUploadError, setResumeUploadError] = useState('')
  const [cityOptions, setCityOptions] = useState<CityOption[]>([])
  const [cityRefresh, setCityRefresh] = useState(false)
  const [cityMessage, setCityMessage] = useState('')

  useEffect(() => {
    fetch('/api/resume').then(r => r.json()).then(setResumeInfo).catch(() => {})
    fetch('/api/cities', { cache: 'no-store' }).then(r => r.json()).then(data => {
      if (Array.isArray(data.cities)) setCityOptions(data.cities)
    }).catch(() => setCityMessage('本地城市列表读取失败'))
  }, [])

  const handleCityRefresh = async () => {
    setCityRefresh(true)
    setCityMessage('')
    try {
      const res = await fetch('/api/cities/refresh', { method: 'POST' })
      const data = await res.json()
      if (Array.isArray(data.cities)) setCityOptions(data.cities)
      if (!res.ok || !data.ok) throw new Error(data.error || '刷新失败，继续使用本地城市数据')
      setCityMessage(`已刷新 ${data.count} 个城市。`)
    } catch (err) {
      setCityMessage(err instanceof Error ? err.message : '刷新失败，继续使用本地城市数据')
    } finally {
      setCityRefresh(false)
    }
  }

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
                  <span className="text-sm text-muted">拖拽或点击上传 (.md、.txt、.pdf、.docx)</span>
                  <input type="file" accept=".md,.txt,.pdf,.docx" onChange={handleResumeUpload} className="hidden" />
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
            <div className="flex items-center justify-between">
              <label className="text-xs text-foreground">接受实习/管培岗位</label>
              <Switch checked={config.profile?.allow_internship ?? false} onChange={v => updateConfig('profile.allow_internship', v)} />
            </div>
          </div>
        </SectionCard>

        {/* Search Section */}
        <SectionCard title="搜索设置" sectionKey="search" expanded={expandedSections} toggle={toggleSection}>
          <div className="space-y-4">
            <Field label="搜索关键词">
              <TagsInput value={config.search?.keywords || []} onChange={v => updateConfig('search.keywords', v)} />
            </Field>
            <Field label="城市">
              <CityMultiSelect
                options={cityOptions}
                value={(config.search?.cities?.length ? config.search.cities : config.profile?.target_cities) || []}
                onChange={cities => { updateConfig('search.cities', cities); updateConfig('profile.target_cities', cities) }}
                onRefresh={handleCityRefresh}
                refreshing={cityRefresh}
                message={cityMessage}
              />
            </Field>
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="每个关键词和城市组合采集数">
                <Input type="number" value={config.search?.target_per_combo || 10} onChange={e => updateConfig('search.target_per_combo', Number(e.target.value))} min={1} max={200} />
                <p className="mt-1 text-xs text-muted">每个组合分别计算新增唯一岗位，重复岗位不计入目标。</p>
              </Field>
              <Field label="最大翻页数（安全上限）">
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

        <SectionCard title="AI 状态摘要" sectionKey="ai-summary" expanded={expandedSections} toggle={toggleSection}>
          <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-card-border bg-[#FFFCFA] p-4">
            <div className="flex items-start gap-3"><Bot className="mt-0.5 h-5 w-5 text-primary" /><div><div className="text-sm font-black">{config.ai?.service || 'anthropic'} · {config.ai?.model || '未设置模型'}</div><p className="mt-1 text-xs text-muted">凭据状态：{config.ai?.api_key_masked || config.ai?.has_api_key ? '已配置（已隐藏）' : '未配置或使用环境变量'}</p></div></div>
            <Link to="/ai-settings" className="inline-flex items-center rounded-xl bg-primary px-3 py-2 text-xs font-black text-white hover:bg-primary/90">打开 AI 设置<ArrowRight className="ml-2 h-3 w-3" /></Link>
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
