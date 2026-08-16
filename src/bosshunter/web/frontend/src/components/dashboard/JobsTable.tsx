import { Fragment, useState } from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { ChevronDown, ChevronUp, Trash2 } from 'lucide-react'
import { getStatusLabel } from '@/lib/status'
import type { Job } from '@/hooks/useDashboard'

interface JobsTableProps {
  jobs: Job[]
  page: number
  pageSize: number
  total: number
  onPageChange: (page: number) => void
  selectedIds: string[]
  onToggleSelected: (id: string) => void
  onSoftDelete?: (job: Job) => void
  loading?: boolean
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

export function JobsTable({ jobs, page, pageSize, total, onPageChange, selectedIds, onToggleSelected, onSoftDelete, loading = false }: JobsTableProps) {
  const [expanded, setExpanded] = useState<string | null>(null)
  const totalPages = Math.ceil(total / pageSize)

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
                <th className="px-4 py-3 text-left font-bold">薪资</th>
                <th className="px-4 py-3 text-left font-bold">评分</th>
                <th className="px-4 py-3 text-left font-bold">状态</th>
                <th className="px-4 py-3 text-left font-bold">招聘者活跃</th>
                <th className="px-4 py-3 text-left font-bold">时间</th>
                {onSoftDelete && <th className="w-16 px-3 py-3 text-center font-bold">操作</th>}
              </tr>
            </thead>
            <tbody>
              {jobs.map(job => {
                const isExpanded = expanded === job.id
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
                          <span className={`rounded-full px-2 py-0.5 text-[10px] font-black ${job.source_platform === 'zhilian' ? 'bg-blue-50 text-blue-700' : 'bg-[#FFF0E5] text-primary'}`}>
                            {job.source_platform === 'zhilian' ? '智联' : 'BOSS'}
                          </span>
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
                      <td className="px-4 py-3 text-xs text-muted">{job.hr_active || '活跃度未知'}</td>
                      <td className="px-4 py-3 text-xs text-muted">
                        <div className="flex items-center gap-2">
                          {timeAgo(job.created_at)}
                          {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                        </div>
                      </td>
                      {onSoftDelete && (
                        <td className="px-3 py-3 text-center" onClick={event => event.stopPropagation()}>
                          <button type="button" onClick={() => onSoftDelete(job)} className="rounded-lg p-2 text-muted hover:bg-red-50 hover:text-danger" aria-label={`将 ${job.company} ${job.title} 移入回收站`}>
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </td>
                      )}
                    </tr>
                    {isExpanded && (
                      <tr className="border-b border-card-border bg-[#FFFCFA]">
                        <td colSpan={onSoftDelete ? 9 : 8} className="px-6 py-4">
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
                  <td colSpan={onSoftDelete ? 9 : 8} className="px-4 py-10 text-center text-sm text-muted">
                    {loading ? '正在读取岗位…' : '没有符合当前条件的岗位'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {totalPages > 1 && (
          <div className="flex items-center justify-between border-t border-card-border px-4 py-3">
            <button
              onClick={() => onPageChange(Math.max(0, page - 1))}
              disabled={page === 0}
              className="text-xs font-bold text-muted transition hover:text-foreground disabled:opacity-30"
            >
              上一页
            </button>
            <span className="text-xs text-muted">{page + 1} / {totalPages}</span>
            <button
              onClick={() => onPageChange(Math.min(totalPages - 1, page + 1))}
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
