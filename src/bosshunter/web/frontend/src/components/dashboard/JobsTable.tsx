import { Fragment, useState } from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { getStatusLabel } from '@/lib/status'
import type { Job } from '@/hooks/useDashboard'

interface JobsTableProps {
  jobs: Job[]
  selectedIds?: string[]
  onSelectionChange?: (ids: string[]) => void
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

export function JobsTable({ jobs, selectedIds = [], onSelectionChange }: JobsTableProps) {
  const [page, setPage] = useState(0)
  const [expanded, setExpanded] = useState<string | null>(null)
  const pageSize = 15
  const totalPages = Math.ceil(jobs.length / pageSize)
  const displayed = jobs.slice(page * pageSize, (page + 1) * pageSize)
  const selectableJobs = displayed.filter(job => job.status === 'pending')
  const toggleSelection = (id: string) => {
    if (!onSelectionChange) return
    onSelectionChange(selectedIds.includes(id) ? selectedIds.filter(item => item !== id) : [...selectedIds, id])
  }
  const toggleAllSelection = () => {
    if (!onSelectionChange) return
    const allSelected = selectableJobs.length > 0 && selectableJobs.every(job => selectedIds.includes(job.id))
    onSelectionChange(allSelected
      ? selectedIds.filter(id => !selectableJobs.some(job => job.id === id))
      : [...new Set([...selectedIds, ...selectableJobs.map(job => job.id)])])
  }

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
                {onSelectionChange && <th className="px-4 py-3 text-left font-bold"><input aria-label="选择待评分岗位" type="checkbox" checked={selectableJobs.length > 0 && selectableJobs.every(job => selectedIds.includes(job.id))} onChange={toggleAllSelection} /></th>}
                <th className="px-4 py-3 text-left font-bold">公司</th>
                <th className="px-4 py-3 text-left font-bold">职位</th>
                <th className="px-4 py-3 text-left font-bold">薪资</th>
                <th className="px-4 py-3 text-left font-bold">评分</th>
                <th className="px-4 py-3 text-left font-bold">状态</th>
                <th className="px-4 py-3 text-left font-bold">时间</th>
              </tr>
            </thead>
            <tbody>
              {displayed.map(job => {
                const isExpanded = expanded === job.id
                const evidence = job.score_evidence
                const evidenceMapping = evidence?.evidence_mapping ?? []
                return (
                  <Fragment key={job.id}>
                    <tr
                      className="cursor-pointer border-b border-card-border bg-white transition-colors hover:bg-[#FFFCFA]"
                      onClick={() => setExpanded(isExpanded ? null : job.id)}
                    >
                      {onSelectionChange && <td className="px-4 py-3"><input aria-label={`选择 ${job.title}`} type="checkbox" disabled={job.status !== 'pending'} checked={selectedIds.includes(job.id)} onClick={event => event.stopPropagation()} onChange={() => toggleSelection(job.id)} /></td>}
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
                      <td className="px-4 py-3 text-xs text-muted">
                        <div className="flex items-center gap-2">
                          {timeAgo(job.created_at)}
                          {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                        </div>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr className="border-b border-card-border bg-[#FFFCFA]">
                        <td colSpan={onSelectionChange ? 7 : 6} className="px-6 py-4">
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
                          {evidence && (
                            <div className="mt-4 rounded-2xl border border-card-border bg-white p-4">
                              <div className="flex flex-wrap items-center justify-between gap-2">
                                <p className="text-xs font-black text-primary">深度评分证据</p>
                                <span className="text-xs text-muted">薪资判断：{evidence.salary_assessment || 'not_provided'}</span>
                              </div>
                              {evidenceMapping.length > 0 ? (
                                <div className="mt-3 space-y-3">
                                  {evidenceMapping.map((item, index) => (
                                    <div key={`${item.requirement}-${index}`} className="border-t border-card-border pt-3 first:border-t-0 first:pt-0">
                                      <p className="font-bold text-foreground">{item.requirement}</p>
                                      <p className="mt-1 leading-6 text-muted">证据：{item.evidence || '未提供'}</p>
                                      <p className="leading-6 text-muted">匹配：{item.match || '未提供'}{item.gap ? `；缺口：${item.gap}` : ''}</p>
                                    </div>
                                  ))}
                                </div>
                              ) : <p className="mt-3 text-muted">本次深度评分未返回可核验的证据映射。</p>}
                            </div>
                          )}
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
