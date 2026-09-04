import { ResumeDualPreview } from '@/components/config/ResumeDualPreview'
import { ResumeFileCard } from '@/components/config/ResumeFileCard'
import {
  afterDropOutside,
  afterResumeLoadFailure,
  afterSuccessfulResumeLoad,
  afterUploadAttempt,
  emptyResumePanelMessages,
  resumeDropOutsideTip,
  resumeLoadErrorMessage,
  resumePanelVisibleMessage,
  shouldSyncResumePath,
  type ResumeInfo,
  type ResumePanelMessages,
} from '@/lib/resumeDisplay'
import { Upload } from 'lucide-react'
import { useCallback, useEffect, useState, type ChangeEvent, type DragEvent } from 'react'

type ResumeUploadSectionProps = {
  currentResumePath?: string
  updateConfig: (path: string, value: unknown, options?: { markDirty?: boolean }) => void
}

/** Resume upload / replace / preview section for the config page. */
export function ResumeUploadSection({ currentResumePath = '', updateConfig }: ResumeUploadSectionProps) {
  const [resumeInfo, setResumeInfo] = useState<ResumeInfo | null>(null)
  const [panelMessages, setPanelMessages] = useState<ResumePanelMessages>(emptyResumePanelMessages)
  const [resumeDragActive, setResumeDragActive] = useState(false)
  const panelMessage = resumePanelVisibleMessage(panelMessages)

  useEffect(() => {
    const preventBrowserNavigation = (event: globalThis.DragEvent) => {
      event.preventDefault()
    }
    const tipWhenDroppedOutsideZone = (event: globalThis.DragEvent) => {
      event.preventDefault()
      setResumeDragActive(false)
      // Zone handlers call stopPropagation; reaching window means drop missed the upload area.
      if (event.dataTransfer?.files?.length) {
        setPanelMessages((prev) => afterDropOutside(prev, resumeDropOutsideTip()))
      }
    }
    window.addEventListener('dragover', preventBrowserNavigation)
    window.addEventListener('drop', tipWhenDroppedOutsideZone)
    return () => {
      window.removeEventListener('dragover', preventBrowserNavigation)
      window.removeEventListener('drop', tipWhenDroppedOutsideZone)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetch('/api/resume')
      .then(async (res) => {
        const data = await res.json().catch(() => null)
        if (cancelled) return
        // Successful empty body (no resume configured) is not an error tip.
        if (res.ok && (data === null || data === undefined)) {
          setResumeInfo(null)
          // Clear load errors only — keep outside-drop tip if the user dropped while loading.
          setPanelMessages((prev) => afterSuccessfulResumeLoad(prev))
          return
        }
        const message = resumeLoadErrorMessage(res.ok, data)
        if (message) {
          setPanelMessages((prev) => afterResumeLoadFailure(prev, message))
          return
        }
        if (data && data.filename) {
          setResumeInfo(data)
          setPanelMessages((prev) => afterSuccessfulResumeLoad(prev))
          // Only sync when server rewrote PDF → MD (or path otherwise drifted).
          if (shouldSyncResumePath(currentResumePath, data.path)) {
            // GET may rewrite PDF→MD on the server; keep local state aligned without dirtying.
            updateConfig('profile.resume_path', data.path, { markDirty: false })
          }
        }
      })
      .catch(() => {
        if (!cancelled) {
          setPanelMessages((prev) => afterResumeLoadFailure(prev, '网络错误，无法读取简历'))
        }
      })
    return () => {
      cancelled = true
    }
  }, [currentResumePath, updateConfig])

  const uploadResumeFile = useCallback(async (file: File) => {
    setPanelMessages(afterUploadAttempt(emptyResumePanelMessages()))
    const form = new FormData()
    form.append('file', file)
    try {
      const res = await fetch('/api/resume/upload', { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok || !data.success) {
        setPanelMessages((prev) => afterResumeLoadFailure(prev, data.error || '简历上传失败'))
        return
      }
      const { success: _success, ...info } = data
      setResumeInfo(info as ResumeInfo)
      // Upload already persisted resume_path server-side.
      updateConfig('profile.resume_path', data.path, { markDirty: false })
    } catch {
      setPanelMessages((prev) => afterResumeLoadFailure(prev, '网络错误，简历上传失败'))
    }
  }, [updateConfig])

  const handleResumeUpload = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      await uploadResumeFile(file)
    } finally {
      e.target.value = ''
    }
  }

  const handleResumeDragOver = (e: DragEvent<HTMLElement>) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
    setResumeDragActive(true)
  }

  const handleResumeDragLeave = (e: DragEvent<HTMLElement>) => {
    e.preventDefault()
    e.stopPropagation()
    setResumeDragActive(false)
  }

  const handleResumeDrop = async (e: DragEvent<HTMLElement>) => {
    e.preventDefault()
    e.stopPropagation()
    setResumeDragActive(false)
    const file = e.dataTransfer.files?.[0]
    if (!file) return
    await uploadResumeFile(file)
  }

  const handleResumeDelete = async () => {
    try {
      const res = await fetch('/api/resume', { method: 'DELETE' })
      const data = await res.json().catch(() => null)
      if (!res.ok || !data?.success) {
        setPanelMessages((prev) =>
          afterResumeLoadFailure(prev, (data && data.error) || '删除简历失败'),
        )
        return
      }
      setResumeInfo(null)
      setPanelMessages(emptyResumePanelMessages())
      // DELETE already cleared resume_path server-side.
      updateConfig('profile.resume_path', '', { markDirty: false })
    } catch {
      setPanelMessages((prev) => afterResumeLoadFailure(prev, '网络错误，删除简历失败'))
    }
  }

  return (
    <div>
      <label className="mb-2 block text-xs text-foreground">简历文件</label>
      {resumeInfo ? (
        <ResumeFileCard
          info={resumeInfo}
          dragActive={resumeDragActive}
          onDelete={handleResumeDelete}
          onDragEnter={handleResumeDragOver}
          onDragOver={handleResumeDragOver}
          onDragLeave={handleResumeDragLeave}
          onDrop={handleResumeDrop}
          footer={<ResumeDualPreview key={`${resumeInfo.path}:${resumeInfo.cache_buster || ''}`} info={resumeInfo} />}
        />
      ) : (
        <label
          className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-6 transition-colors hover:border-primary/50 hover:bg-[#FFFCFA] ${
            resumeDragActive ? 'border-primary bg-[#FFFCFA]' : 'border-card-border'
          }`}
          onDragEnter={handleResumeDragOver}
          onDragOver={handleResumeDragOver}
          onDragLeave={handleResumeDragLeave}
          onDrop={handleResumeDrop}
        >
          <Upload className="mb-2 h-6 w-6 text-muted" />
          <span className="text-sm text-muted">拖拽或点击上传 (.md、.docx、.pdf)</span>
          <input type="file" accept=".md,.docx,.pdf,application/pdf" onChange={handleResumeUpload} className="hidden" />
        </label>
      )}
      {panelMessage ? (
        <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-500">{panelMessage}</p>
      ) : null}
    </div>
  )
}
