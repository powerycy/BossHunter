import { ResumeRevealPanel } from '@/components/config/ResumeRevealPanel'
import {
  hasOriginalPdf,
  resumeOriginalPreviewSrc,
  resumePdfToggleLabel,
  type ResumeInfo,
} from '@/lib/resumeDisplay'

type ResumePdfRevealProps = {
  info: ResumeInfo
  visible: boolean
  onToggle: () => void
}

/** Original PDF iframe preview; visibility controlled by parent. */
export function ResumePdfReveal({ info, visible, onToggle }: ResumePdfRevealProps) {
  if (!hasOriginalPdf(info)) {
    return null
  }

  return (
    <ResumeRevealPanel
      visible={visible}
      onToggle={onToggle}
      label={resumePdfToggleLabel(visible)}
    >
      <iframe
        title={info.original_filename || '原 PDF 预览'}
        src={resumeOriginalPreviewSrc(info)}
        className="h-96 w-full rounded-md border border-card-border bg-white"
      />
    </ResumeRevealPanel>
  )
}
