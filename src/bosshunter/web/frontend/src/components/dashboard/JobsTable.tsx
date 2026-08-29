import { Fragment, useEffect, useState } from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { CheckCircle2, ChevronDown, ChevronUp, ExternalLink, Trash2 } from 'lucide-react'
import { getStatusLabel } from '@/lib/status'
import type { Job } from '@/hooks/useDashboard'
import type { JobSortKey, JobSortOrder } from '@/hooks/useJobSearch'

interface JobsTableProps {
  jobs: Job[]
  page: number
  pageSize: number
  total: number
  onPageChange: (page: number) => void
  selectedIds: string[]
  onToggleSelected: (id: string) => void
  onSoftDelete?: (job: Job) => void
  onMarkManuallySent?: (job: Job) => void
  loading?: boolean
  sortBy: JobSortKey
  sortOrder: JobSortOrder
  onSortChange: (sortBy: JobSortKey) => void
}

function safeJobUrl(job: Job): string | null {
  const platform = job.source_platform || 'boss'
  if (platform !== 'boss' && platform !== 'zhilian' && platform !== '51job') return null
  try {
    const parsed = platform === 'boss'
      ? new URL(job.url || '', 'https://www.zhipin.com')
      : new URL(job.url || '')
    if (parsed.protocol !== 'https:') return null
    const rootDomain = platform === 'boss'
      ? 'zhipin.com'
      : platform === 'zhilian'
        ? 'zhaopin.com'
        : '51job.com'
    if (parsed.hostname !== rootDomain && !parsed.hostname.endsWith(`.${rootDomain}`)) return null
    return parsed.toString()
  } catch {
    return null
  }
}

function statusVariant(status: string) {
  const variants = new Set([
    'pending',
    'scored',
    'filtered',
    'ready',
    'approved',
    'skipped',
    'sent',
    'replied',
    'resume_sent',
    'needs_resume',
    'follow_up_sent',
    'rejected',
    'error',
  ])
  return variants.has(status) ? status : 'default'
}

export function JobsTable({ jobs, page, pageSize, total, onPageChange, selectedIds, onToggleSelected, onSoftDelete, onMarkManuallySent, loading = false, sortBy, sortOrder, onSortChange }: JobsTableProps) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const [pageInput, setPageInput] = useState(String(page + 1))
  const totalPages = Math.ceil(total / pageSize)
  const hasActions = Boolean(onSoftDelete || onMarkManuallySent)

  useEffect(() => {
    setPageInput(String(page + 1))
  }, [page])

  const jumpToPage = () => {
    const requested = Number.parseInt(pageInput, 10)
    if (!Number.isFinite(requested) || totalPages < 1) {
      setPageInput(String(page + 1))
      return
    }
    onPageChange(Math.min(totalPages - 1, Math.max(0, requested - 1)))
  }

  const getScoreColor = (score: number) => {
    if (score >= 80) return 'text-success'
    if (score >= 60) return 'text-primary'
    return 'text-muted'
  }

  const timeAgo = (dateStr: string) => {
    if (!dateStr) return ''
    const normalizedDate = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(dateStr)
      ? `${dateStr.replace(' ', 'T')}Z`
      : dateStr
    const timestamp = new Date(normalizedDate).getTime()
    if (Number.isNaN(timestamp)) return ''
    const diff = Date.now() - timestamp
    const hours = Math.floor(diff / 3600000)
    if (hours < 1) return '刚刚'
    if (hours < 24) return `${hours}h 前`
    return `${Math.floor(hours / 24)}d 前`
  }

  const sortableHeader = (label: string, key: JobSortKey) => (
    <button
      type="button"
      onClick={() => onSortChange(key)}
      className="inline-flex items-center gap-1 font-bold hover:text-primary"
      title={`按${label}排序`}
    >
      {label}<span className="text-[10px]">{sortBy === key ? (sortOrder === 'asc' ? '↑' : '↓') : '↕'}</span>
    </button>
  )

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>岗位列表</CardTitle>
        <span className="text-xs text-muted">{total} 条记录</span>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-card-border bg-[#FFF0E5] text-xs text-muted">
                <th className="w-10 px-3 py-3 text-center font-bold">选</th>
                <th className="px-4 py-3 text-left font-bold">公司</th>
                <th className="px-4 py-3 text-left font-bold">职位</th>
                <th className="px-4 py-3 text-left font-bold">城市</th>
                <th className="px-4 py-3 text-left">{sortableHeader('薪资', 'salary')}</th>
                <th className="px-4 py-3 text-left">{sortableHeader('学历 / 招聘类型', 'education')}</th>
                <th className="px-4 py-3 text-left">{sortableHeader('评分', 'score')}</th>
                <th className="px-4 py-3 text-left">{sortableHeader('状态', 'status')}</th>
                <th className="px-4 py-3 text-left">{sortableHeader('招聘者活跃', 'hr_active')}</th>
                <th className="px-4 py-3 text-left">{sortableHeader('时间', 'created_at')}</th>
                {hasActions && <th className="min-w-[210px] px-3 py-3 text-center font-bold">操作</th>}
              </tr>
            </thead>
            <tbody>
              {jobs.map(job => {
                const isExpanded = expanded === job.id
                const isExternalPlatform = job.source_platform === 'zhilian' || job.source_platform === '51job'
                const jobUrl = safeJobUrl(job)
                const alreadySent = ['sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent'].includes(job.status)
                return (
                  <Fragment key={job.id}>
                    <tr
                      className="cursor-pointer border-b border-card-border bg-white transition-colors hover:bg-[#FFFCFA]"
                      onClick={() => setExpanded(isExpanded ? null : job.id)}
                    >
                      <td className="px-3 py-3 text-center" onClick={event => event.stopPropagation()}>
                        <input
                          type="checkbox"
                          checked={selectedIds.includes(job.id)}
                          onChange={() => onToggleSelected(job.id)}
                          aria-label={`选择 ${job.company} ${job.title}`}
                          className="h-4 w-4 accent-primary"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <span className="max-w-[160px] truncate font-black text-foreground">{job.company}</span>
                          <span className={`rounded-full px-2 py-0.5 text-[10px] font-black ${job.source_platform === 'boss' || !job.source_platform ? 'bg-[#FFF0E5] text-primary' : 'bg-blue-50 text-blue-700'}`}>
                            {job.source_platform === 'zhilian' ? '智联' : job.source_platform === '51job' ? '51job' : 'BOSS'}
                          </span>
                          {job.company_size && (
                            <span className="rounded-full bg-[#FFFCFA] px-2 py-0.5 text-[10px] font-bold text-muted">{job.company_size}</span>
                          )}
                        </div>
                      </td>
                      <td className="max-w-[220px] truncate px-4 py-3 font-bold text-foreground">{job.title}</td>
                      <td className="px-4 py-3 text-muted">{job.city || '未识别'}</td>
                      <td className="px-4 py-3 text-muted">{job.salary || '-'}</td>
                      <td className="px-4 py-3 text-xs">
                        <div className="font-bold text-foreground">{job.education || '学历未识别'}</div>
                        <div className="mt-1 text-muted">{job.recruitment_type === 'campus' ? '校招' : job.recruitment_type === 'experienced' ? '社招' : '类型未识别'}</div>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`font-mono font-black ${getScoreColor(job.score)}`}>{job.score || '-'}</span>
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant={statusVariant(job.status) as any}>{getStatusLabel(job.status)}</Badge>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted">{job.hr_active || '活跃度未知'}</td>
                      <td className="px-4 py-3 text-xs text-muted">
                        <div className="flex items-center gap-2">
                          {timeAgo(job.created_at)}
                          {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                        </div>
                      </td>
                      {hasActions && (
                        <td className="px-3 py-3" onClick={event => event.stopPropagation()}>
                          <div className="flex flex-wrap items-center justify-center gap-1.5">
                            {isExternalPlatform && jobUrl && (
                              <a href={jobUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 rounded-lg border border-card-border px-2 py-1.5 text-[11px] font-bold text-primary hover:bg-[#FFF0E5]">
                                <ExternalLink className="h-3.5 w-3.5" />打开平台
                              </a>
                            )}
                            {isExternalPlatform && !jobUrl && (
                              <span className="rounded-lg bg-amber-50 px-2 py-1.5 text-[11px] font-bold text-amber-700">链接不可用</span>
                            )}
                            {!isExternalPlatform && jobUrl && (
                              <a href={jobUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 rounded-lg border border-card-border px-2 py-1.5 text-[11px] font-bold text-primary hover:bg-[#FFF0E5]">
                                <ExternalLink className="h-3.5 w-3.5" />跳转岗位
                              </a>
                            )}
                            {isExternalPlatform && onMarkManuallySent && (
                              <button
                                type="button"
                                disabled={alreadySent}
                                onClick={() => onMarkManuallySent(job)}
                                className="inline-flex items-center gap-1 rounded-lg bg-primary px-2 py-1.5 text-[11px] font-bold text-white hover:opacity-90 disabled:bg-emerald-50 disabled:text-emerald-700 disabled:opacity-100"
                              >
                                <CheckCircle2 className="h-3.5 w-3.5" />{alreadySent ? '已发送' : '我已发送'}
                              </button>
                            )}
                            {onSoftDelete && (
                              <button type="button" onClick={() => onSoftDelete(job)} className="rounded-lg p-2 text-muted hover:bg-red-50 hover:text-danger" aria-label={`将 ${job.company} ${job.title} 移入回收站`}>
                                <Trash2 className="h-4 w-4" />
                              </button>
                            )}
                          </div>
                        </td>
                      )}
                    </tr>
                    {isExpanded && (
                      <tr className="border-b border-card-border bg-[#FFFCFA]">
                        <td colSpan={hasActions ? 11 : 10} className="px-6 py-4">
                          <div className="grid grid-cols-1 gap-4 text-sm lg:grid-cols-3">
                            <div className="rounded-2xl border border-card-border bg-white p-4">
                              <p className="mb-2 text-xs font-black text-primary">JD摘要</p>
                              <p className="line-clamp-6 leading-6 text-muted">{job.jd || '无'}</p>
                            </div>
                            <div className="rounded-2xl border border-card-border bg-white p-4">
                              <p className="mb-2 text-xs font-black text-primary">招呼语</p>
                              <p className="line-clamp-6 whitespace-pre-wrap leading-6 text-muted">{job.greeting || '未生成'}</p>
                            </div>
                            <div className="rounded-2xl border border-card-border bg-white p-4">
                              <p className="mb-2 text-xs font-black text-primary">评分理由</p>
                              <p className="line-clamp-6 whitespace-pre-wrap leading-6 text-muted">{job.score_reason || '无'}</p>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
              {!jobs.length && (
                <tr>
                  <td colSpan={hasActions ? 11 : 10} className="px-4 py-10 text-center text-sm text-muted">
                    {loading ? '正在读取岗位…' : '没有符合当前条件的岗位'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {totalPages > 0 && (
          <div className="flex flex-wrap items-center justify-center gap-3 border-t border-card-border px-4 py-3 text-xs">
            <button
              onClick={() => onPageChange(0)}
              disabled={page === 0}
              className="font-bold text-muted transition hover:text-foreground disabled:opacity-30"
            >
              首页
            </button>
            <button
              onClick={() => onPageChange(Math.max(0, page - 1))}
              disabled={page === 0}
              className="font-bold text-muted transition hover:text-foreground disabled:opacity-30"
            >
              上一页
            </button>
            <label className="flex items-center gap-1 text-muted">
              第
              <input
                type="number"
                min={1}
                max={Math.max(1, totalPages)}
                value={pageInput}
                onChange={event => setPageInput(event.target.value)}
                onKeyDown={event => { if (event.key === 'Enter') jumpToPage() }}
                onBlur={jumpToPage}
                aria-label="跳转页码"
                className="w-14 rounded-md border border-card-border bg-[#FFFCFA] px-2 py-1 text-center text-foreground outline-none focus:border-primary"
              />
              页 / {totalPages} 页
            </label>
            <button
              onClick={() => onPageChange(Math.min(totalPages - 1, page + 1))}
              disabled={page >= totalPages - 1}
              className="font-bold text-muted transition hover:text-foreground disabled:opacity-30"
            >
              下一页
            </button>
            <button
              onClick={() => onPageChange(totalPages - 1)}
              disabled={page >= totalPages - 1}
              className="font-bold text-muted transition hover:text-foreground disabled:opacity-30"
            >
              尾页
            </button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
