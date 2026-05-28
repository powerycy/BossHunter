import { useState } from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { ChevronDown, ChevronUp } from 'lucide-react'
import type { Job } from '@/hooks/useDashboard'

interface JobsTableProps {
  jobs: Job[]
}

const STATUS_LABELS: Record<string, string> = {
  pending: '待处理',
  scored: '已评分',
  filtered: '已过滤',
  approved: '已确认',
  sent: '已发送',
  replied: '已回复',
  resume_sent: '简历已发',
  follow_up_sent: '已跟进',
  rejected: '已拒绝',
  error: '错误',
}

export function JobsTable({ jobs }: JobsTableProps) {
  const [page, setPage] = useState(0)
  const [expanded, setExpanded] = useState<string | null>(null)
  const pageSize = 15
  const totalPages = Math.ceil(jobs.length / pageSize)
  const displayed = jobs.slice(page * pageSize, (page + 1) * pageSize)

  const getScoreColor = (score: number) => {
    if (score >= 80) return 'text-green-400'
    if (score >= 60) return 'text-blue-400'
    return 'text-zinc-500'
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
        <span className="text-xs text-zinc-500">{jobs.length} 条记录</span>
      </CardHeader>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-zinc-400 text-xs">
                <th className="text-left px-4 py-3 font-medium">公司</th>
                <th className="text-left px-4 py-3 font-medium">职位</th>
                <th className="text-left px-4 py-3 font-medium">薪资</th>
                <th className="text-left px-4 py-3 font-medium">评分</th>
                <th className="text-left px-4 py-3 font-medium">状态</th>
                <th className="text-left px-4 py-3 font-medium">时间</th>
              </tr>
            </thead>
            <tbody>
              {displayed.map(job => (
                <>
                  <tr
                    key={job.id}
                    className="border-b border-zinc-800/50 hover:bg-zinc-800/30 cursor-pointer transition-colors"
                    onClick={() => setExpanded(expanded === job.id ? null : job.id)}
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="text-zinc-100 truncate max-w-[120px]">{job.company}</span>
                        {job.company_size && (
                          <span className="text-[10px] text-zinc-500 bg-zinc-800 px-1.5 py-0.5 rounded">{job.company_size}</span>
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-zinc-200 truncate max-w-[180px]">{job.title}</td>
                    <td className="px-4 py-3 text-zinc-300">{job.salary}</td>
                    <td className="px-4 py-3">
                      <span className={`font-mono font-bold ${getScoreColor(job.score)}`}>{job.score || '-'}</span>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={job.status as any}>{STATUS_LABELS[job.status] || job.status}</Badge>
                    </td>
                    <td className="px-4 py-3 text-zinc-500 text-xs">{timeAgo(job.created_at)}</td>
                  </tr>
                  {expanded === job.id && (
                    <tr key={`${job.id}-detail`} className="bg-zinc-800/20">
                      <td colSpan={6} className="px-6 py-4">
                        <div className="grid grid-cols-2 gap-4 text-xs">
                          <div>
                            <p className="text-zinc-400 mb-1">JD 摘要</p>
                            <p className="text-zinc-300 line-clamp-4">{job.jd || '无'}</p>
                          </div>
                          <div>
                            <p className="text-zinc-400 mb-1">招呼语</p>
                            <p className="text-zinc-300">{job.greeting || '未生成'}</p>
                            {job.score_reason && (
                              <>
                                <p className="text-zinc-400 mt-2 mb-1">评分理由</p>
                                <p className="text-zinc-300">{job.score_reason}</p>
                              </>
                            )}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-zinc-800">
            <button
              onClick={() => setPage(p => Math.max(0, p - 1))}
              disabled={page === 0}
              className="text-xs text-zinc-400 hover:text-white disabled:opacity-30"
            >
              上一页
            </button>
            <span className="text-xs text-zinc-500">{page + 1} / {totalPages}</span>
            <button
              onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="text-xs text-zinc-400 hover:text-white disabled:opacity-30"
            >
              下一页
            </button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
