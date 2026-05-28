import { useDashboard } from '@/hooks/useDashboard'
import { PipelineFlow } from '@/components/dashboard/PipelineFlow'
import { FunnelCards } from '@/components/dashboard/FunnelCards'
import { TrendChart } from '@/components/dashboard/TrendChart'
import { JobsTable } from '@/components/dashboard/JobsTable'
import { TopCompanies } from '@/components/dashboard/TopCompanies'
import { RefreshCw } from 'lucide-react'

export default function DashboardPage() {
  const { funnel, activity, jobs, topCompanies, loading, refresh } = useDashboard()

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-zinc-400 text-sm">加载中...</div>
      </div>
    )
  }

  const isEmpty = !jobs.length && !Object.values(funnel).some(v => v > 0)

  if (isEmpty) {
    return (
      <div className="space-y-6">
        <PipelineFlow />
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="text-4xl mb-4">📋</div>
          <h2 className="text-lg font-medium text-zinc-200 mb-2">暂无数据</h2>
          <p className="text-sm text-zinc-400 max-w-md">
            请先运行 <code className="bg-zinc-800 px-2 py-0.5 rounded text-zinc-200">bosshunter run</code> 启动完整流程，
            或使用 <code className="bg-zinc-800 px-2 py-0.5 rounded text-zinc-200">bosshunter scrape</code> 开始采集岗位。
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Pipeline flow */}
      <PipelineFlow />

      {/* Funnel stats */}
      <FunnelCards data={funnel} />

      {/* Charts row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <TrendChart data={activity} />
        </div>
        <TopCompanies data={topCompanies} />
      </div>

      {/* Jobs table */}
      <JobsTable jobs={jobs} />
    </div>
  )
}
