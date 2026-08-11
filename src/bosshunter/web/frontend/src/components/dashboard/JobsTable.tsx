import { Fragment, useState } from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { ChevronDown, ChevronUp, ExternalLink } from 'lucide-react'
import { getStatusLabel } from '@/lib/status'
import type { Job } from '@/hooks/useDashboard'

interface JobsTableProps {
  jobs: Job[]
  selectedIds?: string[]
  onToggleSelected?: (id: string) => void
  onOverrideFilter?: (job: Job) => void
  showDeleted?: boolean
  onSoftDelete?: (job: Job) => void
  onRestore?: (job: Job) => void
  onPermanentDelete?: (job: Job) => void
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

export function JobsTable({
  jobs,
  selectedIds = [],
  onToggleSelected,
  onOverrideFilter,
  showDeleted = false,
  onSoftDelete,
  onRestore,
  onPermanentDelete,
}: JobsTableProps) {
  const [page, setPage] = useState(0)
  const [expanded, setExpanded] = useState<string | null>(null)
  const pageSize = 15
  const totalPages = Math.ceil(jobs.length / pageSize)
  const displayed = jobs.slice(page * pageSize, (page + 1) * pageSize)
  const hasActions = Boolean(onSoftDelete || onRestore || onPermanentDelete)
  const columnCount = (onToggleSelected ? 1 : 0) + 7 + (hasActions ? 1 : 0)

  const getScoreColor = (score: number) => {
    if (score >= 80) return 'text-success'
    if (score >= 60) return 'text-primary'
    return 'text-muted'
  }

  const timeAgo = (dateStr: string) => {
    if (!dateStr) return ''
    const diff = Date.now() - new Date(dateStr).getTime()
    const hours = Math.floor(diff / 3600000)
    if (hours < 1) return '刚刚'
    if (hours < 24) return `${hours}h 前`
    return `${Math.floor(hours / 24)}d 前`
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>岗位列表</CardTitle>
        <span className="text-xs text-muted">{jobs.length} 条记录</span>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-card-border bg-[#FFF0E5] text-xs text-muted">
                {onToggleSelected && <th className="px-4 py-3 text-left font-bold">选</th>}
                <th className="px-4 py-3 text-left font-bold">公司</th>
                <th className="px-4 py-3 text-left font-bold">职位</th>
                <th className="px-4 py-3 text-left font-bold">薪资</th>
                <th className="px-4 py-3 text-left font-bold">评分</th>
                <th className="px-4 py-3 text-left font-bold">状态</th>
                <th className="px-4 py-3 text-left font-bold">原岗位</th>
                <th className="px-4 py-3 text-left font-bold">时间</th>
                {hasActions && <th className="px-4 py-3 text-left font-bold">操作</th>}
              </tr>
            </thead>
            <tbody>
              {displayed.map(job => {
                const isExpanded = expanded === job.id
                return (
                  <Fragment key={job.id}>
                    <tr
                      className="cursor-pointer border-b border-card-border bg-white transition-colors hover:bg-[#FFFCFA]"
                      onClick={() => setExpanded(isExpanded ? null : job.id)}
                    >
                      {onToggleSelected && (
                        <td className="px-4 py-3" onClick={event => event.stopPropagation()}>
                          <input type="checkbox" checked={selectedIds.includes(job.id)} onChange={() => onToggleSelected(job.id)} className="h-4 w-4 accent-primary" aria-label={`选择 ${job.company} ${job.title}`} />
                        </td>
                      )}
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <span className="max-w-[160px] truncate font-black text-foreground">{job.company}</span>
                          {job.company_size && (
                            <span className="rounded-full bg-[#FFFCFA] px-2 py-0.5 text-[10px] font-bold text-muted">{job.company_size}</span>
                          )}
                        </div>
                      </td>
                      <td className="max-w-[220px] truncate px-4 py-3 font-bold text-foreground">{job.title}</td>
                      <td className="px-4 py-3 text-muted">{job.salary || '-'}</td>
                      <td className="px-4 py-3">
                        <span className={`font-mono font-black ${getScoreColor(job.score)}`}>{job.score || '-'}</span>
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant={statusVariant(job.status) as any}>{getStatusLabel(job.status)}</Badge>
                      </td>
                      <td className="px-4 py-3" onClick={event => event.stopPropagation()}>
                        <div className="flex flex-wrap gap-2">
                          {job.url ? <a href={job.url} target="_blank" rel="noopener,noreferrer" className="inline-flex items-center gap-1 text-xs font-black text-primary hover:underline"><ExternalLink className="h-3 w-3" />打开原岗位</a> : <span className="text-xs text-muted">链接不可用</span>}
                          {job.status === 'filtered' && job.filter_source && onOverrideFilter && <button type="button" onClick={() => onOverrideFilter(job)} className="text-xs font-black text-amber-700 hover:underline">仍要推进</button>}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted">
                        <div className="flex items-center gap-2">
                          {timeAgo(job.created_at)}
                          {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                        </div>
                      </td>
                      {hasActions && (
                        <td className="px-4 py-3" onClick={event => event.stopPropagation()}>
                          <div className="flex flex-wrap gap-2">
                            {!showDeleted && onSoftDelete && (
                              <button type="button" onClick={() => onSoftDelete(job)} className="text-xs font-black text-danger hover:underline">
                                移入回收站
                              </button>
                            )}
                            {showDeleted && onRestore && (
                              <button type="button" onClick={() => onRestore(job)} className="text-xs font-black text-primary hover:underline">
                                恢复
                              </button>
                            )}
                            {showDeleted && onPermanentDelete && job.permanent_delete_allowed !== false && (
                              <button type="button" onClick={() => onPermanentDelete(job)} className="text-xs font-black text-danger hover:underline">
                                永久删除
                              </button>
                            )}
                            {showDeleted && job.permanent_delete_allowed === false && (
                              <span className="text-xs font-bold text-muted" title={(job.permanent_delete_reasons || []).join('；')}>
                                仅保留历史
                              </span>
                            )}
                          </div>
                        </td>
                      )}
                    </tr>
                    {isExpanded && (
                      <tr className="border-b border-card-border bg-[#FFFCFA]">
                        <td colSpan={columnCount} className="px-6 py-4">
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
                              {job.score_failure_json && <p className="mt-2 text-xs text-danger">最近评分失败：{job.score_failure_json}</p>}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>

        {totalPages > 1 && (
          <div className="flex items-center justify-between border-t border-card-border px-4 py-3">
            <button
              onClick={() => setPage(p => Math.max(0, p - 1))}
              disabled={page === 0}
              className="text-xs font-bold text-muted transition hover:text-foreground disabled:opacity-30"
            >
              上一页
            </button>
            <span className="text-xs text-muted">{page + 1} / {totalPages}</span>
            <button
              onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="text-xs font-bold text-muted transition hover:text-foreground disabled:opacity-30"
            >
              下一页
            </button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
