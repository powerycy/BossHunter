import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Check,
  Download,
  FileText,
  Loader2,
  Sparkles,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

type FactStatus = 'pending' | 'accepted' | 'rejected'

interface ResumeSource {
  id: string
  filename: string
  source_type: string
  status: string
  error?: string | null
  fact_count: number
  accepted_count: number
  pending_count: number
  created_at: string
}

interface ResumeFact {
  id: string
  source_id: string
  source_filename: string
  category: string
  content: string
  effective_content: string
  evidence: string
  confidence: number
  status: FactStatus
}

interface ResumeVersion {
  id: string
  name: string
  target_role?: string
  markdown: string
  file_path: string
  status: 'draft' | 'active'
  fact_count: number
  created_at: string
}

interface Workspace {
  sources: ResumeSource[]
  facts: ResumeFact[]
  versions: ResumeVersion[]
}

const emptyWorkspace: Workspace = { sources: [], facts: [], versions: [] }

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  const data = await response.json()
  if (!response.ok) throw new Error(data.error || '操作失败')
  return data as T
}

export default function ResumeStudioPage() {
  const [workspace, setWorkspace] = useState<Workspace>(emptyWorkspace)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [factFilter, setFactFilter] = useState<FactStatus>('pending')
  const [targetRole, setTargetRole] = useState('')

  const loadWorkspace = useCallback(async () => {
    try {
      setWorkspace(await api<Workspace>('/api/resume-studio'))
    } catch (error) {
      setMessage({ ok: false, text: error instanceof Error ? error.message : '简历工作室加载失败' })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadWorkspace()
  }, [loadWorkspace])

  const acceptedCount = workspace.facts.filter(fact => fact.status === 'accepted').length
  const visibleFacts = useMemo(
    () => workspace.facts.filter(fact => fact.status === factFilter),
    [factFilter, workspace.facts],
  )
  const latestVersion = workspace.versions[0]

  const run = async (key: string, action: () => Promise<string>) => {
    setBusy(key)
    setMessage(null)
    try {
      setMessage({ ok: true, text: await action() })
      await loadWorkspace()
    } catch (error) {
      setMessage({ ok: false, text: error instanceof Error ? error.message : '操作失败' })
    } finally {
      setBusy('')
    }
  }

  const uploadSources = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || [])
    if (!files.length) return
    await run('upload', async () => {
      let duplicates = 0
      for (const file of files) {
        const form = new FormData()
        form.append('file', file)
        const result = await api<{ duplicate: boolean }>('/api/resume-studio/sources', {
          method: 'POST',
          body: form,
        })
        if (result.duplicate) duplicates += 1
      }
      return `已处理 ${files.length} 份材料${duplicates ? `，其中 ${duplicates} 份为重复材料` : ''}`
    })
    event.target.value = ''
  }

  const extractFacts = (source: ResumeSource) => run(`extract:${source.id}`, async () => {
    const result = await api<{ facts: ResumeFact[] }>(`/api/resume-studio/sources/${source.id}/extract`, {
      method: 'POST',
    })
    setFactFilter('pending')
    return `已从「${source.filename}」提取 ${result.facts.length} 条待审核事实`
  })

  const deleteSource = (source: ResumeSource) => {
    if (!window.confirm(`确认删除材料「${source.filename}」及其未引用事实？`)) return
    return run(`delete:${source.id}`, async () => {
      await api(`/api/resume-studio/sources/${source.id}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirmed: true }),
      })
      return `已删除材料「${source.filename}」`
    })
  }

  const reviewFact = (fact: ResumeFact, status: FactStatus) => run(`fact:${fact.id}`, async () => {
    await api(`/api/resume-studio/facts/${fact.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status, content: drafts[fact.id] ?? fact.effective_content }),
    })
    setDrafts(current => {
      const next = { ...current }
      delete next[fact.id]
      return next
    })
    return status === 'accepted' ? '事实已接受并进入生成范围' : status === 'rejected' ? '事实已拒绝' : '事实已退回待审核'
  })

  const compose = () => run('compose', async () => {
    const result = await api<{ version: ResumeVersion }>('/api/resume-studio/compose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_role: targetRole }),
    })
    return `已生成「${result.version.name}」，请预览后再启用`
  })

  const activate = (version: ResumeVersion) => run(`activate:${version.id}`, async () => {
    await api(`/api/resume-studio/versions/${version.id}/activate`, { method: 'POST' })
    return `已启用「${version.name}」作为主简历`
  })

  if (loading) {
    return <div className="flex h-full items-center justify-center text-sm text-muted">加载简历工作室...</div>
  }

  return (
    <div className="space-y-5 pb-10">
      <div>
        <h1 className="text-xl font-black text-foreground">简历工作室</h1>
        <p className="mt-1 text-sm text-muted">
          从技术文档和作品材料提取带原文证据的事实。只有你接受的事实才会进入主简历。
        </p>
      </div>

      {message && (
        <div className={`rounded-xl px-4 py-3 text-sm ${message.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600'}`}>
          {message.text}
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(320px,0.8fr)_minmax(520px,1.2fr)]">
        <div className="space-y-5">
          <Card>
            <CardHeader>
              <CardTitle>1. 添加个人材料</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <label className="flex cursor-pointer flex-col items-center rounded-xl border-2 border-dashed border-card-border p-6 hover:border-primary/50 hover:bg-[#FFFCFA]">
                {busy === 'upload' ? <Loader2 className="mb-2 h-6 w-6 animate-spin text-primary" /> : <Upload className="mb-2 h-6 w-6 text-muted" />}
                <span className="text-sm font-bold">批量上传技术文档或作品材料</span>
                <span className="mt-1 text-xs text-muted">支持 .md、.docx、带文字层的 .pdf；单文件不超过 10MB</span>
                <input
                  className="hidden"
                  type="file"
                  multiple
                  disabled={Boolean(busy)}
                  accept=".md,.docx,.pdf,application/pdf"
                  onChange={uploadSources}
                />
              </label>

              {workspace.sources.length === 0 ? (
                <p className="py-4 text-center text-xs text-muted">尚未添加材料</p>
              ) : workspace.sources.map(source => (
                <div key={source.id} className="rounded-xl border border-card-border p-3">
                  <div className="flex items-start gap-3">
                    <FileText className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-bold">{source.filename}</p>
                      <p className="mt-1 text-xs text-muted">
                        {source.fact_count || 0} 条事实 · {source.accepted_count || 0} 条已接受
                      </p>
                      {source.error && <p className="mt-2 text-xs text-red-500">{source.error}</p>}
                    </div>
                    <button
                      className="text-muted hover:text-red-500"
                      title="删除材料"
                      disabled={Boolean(busy)}
                      onClick={() => deleteSource(source)}
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                  <Button
                    className="mt-3 w-full"
                    variant="secondary"
                    size="sm"
                    disabled={Boolean(busy)}
                    onClick={() => extractFacts(source)}
                  >
                    {busy === `extract:${source.id}` ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Sparkles className="mr-1 h-3 w-3" />}
                    {source.fact_count ? '重新提取待审核事实' : '提取事实'}
                  </Button>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>3. 生成主简历</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div>
                <label className="mb-1 block text-xs text-muted">目标方向（可选）</label>
                <Input value={targetRole} onChange={event => setTargetRole(event.target.value)} placeholder="如：Python 后端工程师" maxLength={100} />
              </div>
              <p className="text-xs text-muted">当前有 {acceptedCount} 条已接受事实可用于生成。</p>
              <Button className="w-full" disabled={Boolean(busy) || acceptedCount === 0} onClick={compose}>
                {busy === 'compose' ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Sparkles className="mr-1 h-4 w-4" />}
                生成可审阅草稿
              </Button>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-5">
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <CardTitle>2. 审核材料事实</CardTitle>
                <div className="flex gap-1">
                  {(['pending', 'accepted', 'rejected'] as FactStatus[]).map(status => (
                    <Button
                      key={status}
                      variant={factFilter === status ? 'default' : 'ghost'}
                      size="sm"
                      onClick={() => setFactFilter(status)}
                    >
                      {status === 'pending' ? '待审核' : status === 'accepted' ? '已接受' : '已拒绝'}
                      （{workspace.facts.filter(fact => fact.status === status).length}）
                    </Button>
                  ))}
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {visibleFacts.length === 0 ? (
                <p className="py-8 text-center text-sm text-muted">该分组暂无事实</p>
              ) : visibleFacts.map(fact => (
                <div key={fact.id} className="rounded-xl border border-card-border p-4">
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <span className="rounded-full bg-[#FFF0E5] px-2 py-1 text-xs font-bold text-primary">{fact.category}</span>
                    <span className="truncate text-xs text-muted">来源：{fact.source_filename}</span>
                  </div>
                  <textarea
                    className="min-h-20 w-full rounded-lg border border-card-border bg-white p-3 text-sm outline-none focus:border-primary"
                    value={drafts[fact.id] ?? fact.effective_content}
                    onChange={event => setDrafts(current => ({ ...current, [fact.id]: event.target.value }))}
                    maxLength={1000}
                  />
                  <div className="mt-2 rounded-lg bg-[#FFFCFA] p-3">
                    <p className="text-[11px] font-bold text-muted">原文证据 · 置信度 {Math.round(fact.confidence * 100)}%</p>
                    <p className="mt-1 text-xs leading-5 text-muted">{fact.evidence}</p>
                  </div>
                  <div className="mt-3 flex justify-end gap-2">
                    {fact.status !== 'rejected' && (
                      <Button variant="ghost" size="sm" disabled={Boolean(busy)} onClick={() => reviewFact(fact, 'rejected')}>
                        <X className="mr-1 h-3 w-3" />拒绝
                      </Button>
                    )}
                    {fact.status !== 'pending' && (
                      <Button variant="secondary" size="sm" disabled={Boolean(busy)} onClick={() => reviewFact(fact, 'pending')}>
                        退回待审核
                      </Button>
                    )}
                    <Button size="sm" disabled={Boolean(busy)} onClick={() => reviewFact(fact, 'accepted')}>
                      <Check className="mr-1 h-3 w-3" />{fact.status === 'accepted' ? '保存修改' : '接受'}
                    </Button>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>4. 预览与启用</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {!latestVersion ? (
                <p className="py-8 text-center text-sm text-muted">接受事实并生成后，可在这里审阅主简历。</p>
              ) : (
                <>
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <p className="text-sm font-black">{latestVersion.name}</p>
                      <p className="mt-1 text-xs text-muted">{latestVersion.fact_count} 条事实 · {latestVersion.target_role || '通用方向'}</p>
                    </div>
                    <div className="flex gap-2">
                      <a href={`/api/resume-studio/versions/${latestVersion.id}/download`}>
                        <Button variant="secondary" size="sm"><Download className="mr-1 h-3 w-3" />下载</Button>
                      </a>
                      <Button
                        size="sm"
                        disabled={Boolean(busy) || latestVersion.status === 'active'}
                        onClick={() => activate(latestVersion)}
                      >
                        {latestVersion.status === 'active' ? '当前已启用' : '确认启用'}
                      </Button>
                    </div>
                  </div>
                  <pre className="max-h-[560px] overflow-auto whitespace-pre-wrap rounded-xl border border-card-border bg-[#FFFCFA] p-5 text-sm leading-6">
                    {latestVersion.markdown}
                  </pre>
                  {workspace.versions.length > 1 && (
                    <div className="border-t border-card-border pt-3">
                      <p className="mb-2 text-xs font-bold text-muted">历史版本</p>
                      {workspace.versions.slice(1).map(version => (
                        <div key={version.id} className="flex items-center justify-between py-1 text-xs text-muted">
                          <span>{version.name}{version.status === 'active' ? ' · 已启用' : ''}</span>
                          <a className="text-primary hover:underline" href={`/api/resume-studio/versions/${version.id}/download`}>下载</a>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
