import { useMemo } from 'react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import type { ActivityData } from '@/hooks/useDashboard'

interface TrendChartProps {
  data: ActivityData[]
}

export function TrendChart({ data }: TrendChartProps) {
  const chartData = useMemo(() => {
    const dayMap: Record<string, { day: string; send: number; reply: number }> = {}

    // Generate last 7 days
    for (let i = 6; i >= 0; i--) {
      const d = new Date()
      d.setDate(d.getDate() - i)
      const key = d.toISOString().split('T')[0]
      dayMap[key] = { day: key.slice(5), send: 0, reply: 0 }
    }

    // Fill in actual data
    data.forEach(item => {
      const key = item.day
      if (!dayMap[key]) dayMap[key] = { day: key.slice(5), send: 0, reply: 0 }
      if (item.action === 'sent') dayMap[key].send += item.cnt
      else if (item.action === 'replied' || item.action === 'auto_replied') dayMap[key].reply += item.cnt
    })

    return Object.values(dayMap).sort((a, b) => a.day.localeCompare(b.day))
  }, [data])

  return (
    <Card>
      <CardHeader>
        <CardTitle>7 日趋势</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-[200px]">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
              <XAxis dataKey="day" stroke="#71717a" fontSize={12} />
              <YAxis stroke="#71717a" fontSize={12} />
              <Tooltip
                contentStyle={{ background: '#18181b', border: '1px solid #27272a', borderRadius: '6px' }}
                labelStyle={{ color: '#a1a1aa' }}
              />
              <Legend wrapperStyle={{ fontSize: '12px' }} />
              <Line type="monotone" dataKey="send" name="发送" stroke="#22c55e" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="reply" name="回复" stroke="#f59e0b" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  )
}
