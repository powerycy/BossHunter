import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import type { TopCompany } from '@/hooks/useDashboard'

interface TopCompaniesProps {
  data: TopCompany[]
}

export function TopCompanies({ data }: TopCompaniesProps) {
  if (!data.length) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>高分公司 TOP5</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-zinc-500">暂无数据</p>
        </CardContent>
      </Card>
    )
  }

  const maxScore = Math.max(...data.map(d => d.avg_score))

  return (
    <Card>
      <CardHeader>
        <CardTitle>高分公司 TOP5</CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        <div className="space-y-3">
          {data.map((company, i) => (
            <div key={i} className="flex items-center gap-3">
              <span className="text-xs text-zinc-500 w-4">{i + 1}</span>
              <div className="flex-1">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm text-zinc-200 truncate max-w-[140px]">{company.company}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-zinc-400">{company.job_count} 岗</span>
                    <span className="text-xs font-mono text-blue-400">{company.avg_score}</span>
                  </div>
                </div>
                <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary rounded-full transition-all"
                    style={{ width: `${(company.avg_score / maxScore) * 100}%` }}
                  />
                </div>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
