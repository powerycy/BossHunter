import { RotateCcw, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { JobsTable } from '@/components/dashboard/JobsTable'
import type { Job } from '@/hooks/useDashboard'

interface RecycleBinPanelProps {
  jobs: Job[]
  selectedIds: string[]
  loading: boolean
  onToggleSelected: (id: string) => void
  onSelectAll: (ids: string[]) => void
  onRestore: (job: Job) => void
  onPermanentDelete: (job: Job) => void
  onBatchRestore: () => void
  onBatchPermanentDelete: () => void
}

export function RecycleBinPanel({
  jobs,
  selectedIds,
  loading,
  onToggleSelected,
  onSelectAll,
  onRestore,
  onPermanentDelete,
  onBatchRestore,
  onBatchPermanentDelete,
}: RecycleBinPanelProps) {
  const allSelected = jobs.length > 0 && jobs.every(job => selectedIds.includes(job.id))

  return (
    <div className="rounded-3xl border border-card-border bg-white p-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Trash2 className="h-5 w-5 text-primary" />
            <h2 className="text-2xl font-black">回收站</h2>
          </div>
          <p className="mt-1 text-sm text-muted">软删除岗位保留原状态、评分、招呼语、链接和历史；恢复不会自动执行任何任务。</p>
        </div>
        <span className="rounded-full bg-[#FFF0E5] px-3 py-2 text-xs font-black text-primary">已删除 {jobs.length} 条</span>
      </div>
      {loading ? (
        <div className="rounded-2xl border border-card-border bg-[#FFFCFA] p-6 text-center text-sm text-muted">正在读取回收站...</div>
      ) : (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-2 text-xs">
            <Button
              variant="secondary"
              size="sm"
              disabled={!jobs.length}
              onClick={() => onSelectAll(allSelected ? [] : jobs.map(job => job.id))}
            >
              {allSelected ? '取消全选' : '全选回收站'}
            </Button>
            <span className="rounded-full bg-[#FFF0E5] px-3 py-2 font-bold text-primary">已选择 {selectedIds.length} 条</span>
            <Button variant="secondary" size="sm" disabled={!selectedIds.length} onClick={onBatchRestore}>
              <RotateCcw className="mr-1 h-3 w-3" />批量恢复
            </Button>
            <Button variant="destructive" size="sm" disabled={!selectedIds.length} onClick={onBatchPermanentDelete}>
              <Trash2 className="mr-1 h-3 w-3" />批量永久删除
            </Button>
          </div>
          <JobsTable
            jobs={jobs}
            selectedIds={selectedIds}
            onToggleSelected={onToggleSelected}
            showDeleted
            onRestore={onRestore}
            onPermanentDelete={onPermanentDelete}
          />
        </>
      )}
    </div>
  )
}
