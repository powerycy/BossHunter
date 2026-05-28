import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import type { FunnelData } from '@/hooks/useDashboard'

interface FunnelCardsProps {
  data: FunnelData
}

const funnelSteps = [
  { key: '采集总数', color: 'text-blue-400' },
  { key: '初筛通过', color: 'text-cyan-400' },
  { key: 'AI评分', color: 'text-green-400' },
  { key: '人工确认', color: 'text-amber-400' },
]

export function FunnelCards({ data }: FunnelCardsProps) {
  const total = data['采集总数'] || 0

  return (
    <div className="grid grid-cols-4 gap-3">
      {funnelSteps.map((step, i) => {
        const count = data[step.key] || 0
        const prevCount = i === 0 ? count : (data[funnelSteps[i - 1].key] || 0)
        const rate = prevCount > 0 && i > 0 ? ((count / prevCount) * 100).toFixed(1) + '%' : ''

        return (
          <Card key={step.key} className="border-zinc-800">
            <CardHeader className="pb-2 p-4">
              <CardTitle className="text-xs">{step.key}</CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <div className={`text-2xl font-bold ${step.color}`}>{count}</div>
              {rate && <p className="text-xs text-zinc-500 mt-1">{rate}</p>}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}
