import { Button } from '@/components/ui/button'
import { resumeSizeLabel, type ResumeInfo } from '@/lib/resumeDisplay'
import { Trash2 } from 'lucide-react'
import type { DragEvent, ReactNode } from 'react'

type ResumeFileCardProps = {
  info: ResumeInfo
  dragActive: boolean
  onDelete: () => void
  onDragEnter: (e: DragEvent<HTMLElement>) => void
  onDragOver: (e: DragEvent<HTMLElement>) => void
  onDragLeave: (e: DragEvent<HTMLElement>) => void
  onDrop: (e: DragEvent<HTMLElement>) => void
  footer?: ReactNode
}

/** Uploaded resume status card with optional replace-via-drop zone. */
export function ResumeFileCard({
  info,
  dragActive,
  onDelete,
  onDragEnter,
  onDragOver,
  onDragLeave,
  onDrop,
  footer,
}: ResumeFileCardProps) {
  return (
    <div
      className={`rounded-md border bg-[#FFFCFA] p-3 transition-colors ${
        dragActive ? 'border-primary' : 'border-card-border'
      }`}
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
    >
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-bold text-foreground">{info.filename}</div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
            <span>{resumeSizeLabel(info)}</span>
            {info.uploaded_at ? <span>上传于 {info.uploaded_at}</span> : null}
            <span className="font-bold text-emerald-700">已上传</span>
            {info.has_original_pdf ? <span>含原 PDF</span> : null}
          </div>
        </div>
        <Button type="button" variant="ghost" size="icon" onClick={onDelete} aria-label="删除简历">
          <Trash2 className="h-4 w-4 text-red-400" />
        </Button>
      </div>
      {footer}
    </div>
  )
}
