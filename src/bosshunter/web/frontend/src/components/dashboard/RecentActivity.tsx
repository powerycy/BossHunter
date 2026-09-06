import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import type { HistoryItem } from '@/hooks/useDashboard'

interface RecentActivityProps {
  data: HistoryItem[]
}

const ACTION_LABELS: Record<string, string> = {
  scrape: '采集',
  scored: '评分',
  sent: '发送',
  manual_sent: '手动已发送',
  replied: '回复',
  hr_reply_detected: 'HR 消息',
  auto_replied: '自动回复',
  error: '错误',
  approved: '确认',
  filtered: '过滤',
  resume_sent: '简历',
}

export function RecentActivity({ data }: RecentActivityProps) {
  const formatTime = (dateStr: string) => {
    if (!dateStr) return ''
    const d = new Date(dateStr)
    return `${(d.getMonth() + 1).toString().padStart(2, '0')}-${d.getDate().toString().padStart(2, '0')} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
  }

  if (!data.length) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>最近活动</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-zinc-500">暂无活动记录</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>最近活动</CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        <div className="space-y-3">
          {data.map((item, i) => (
            <div key={i} className="flex items-start gap-3">
              <div className="w-1.5 h-1.5 rounded-full bg-zinc-600 mt-2 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <Badge variant={item.action as any} className="text-[10px] px-1.5 py-0">
                    {ACTION_LABELS[item.action] || item.action}
                  </Badge>
                  <span className="text-xs text-zinc-300 truncate">
                    {item.company} · {item.title}
                  </span>
                </div>
                <p className="text-[11px] text-zinc-500 mt-0.5">{formatTime(item.created_at)}</p>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
