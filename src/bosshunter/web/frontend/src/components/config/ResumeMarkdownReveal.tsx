import { ResumeRevealPanel } from '@/components/config/ResumeRevealPanel'
import {
  hasResumePreviewContent,
  resumeMarkdownToggleLabel,
  type ResumeInfo,
} from '@/lib/resumeDisplay'

type ResumeMarkdownRevealProps = {
  info: ResumeInfo
  visible: boolean
  onToggle: () => void
}

/** Converted Markdown preview; visibility controlled by parent. */
export function ResumeMarkdownReveal({ info, visible, onToggle }: ResumeMarkdownRevealProps) {
  if (!hasResumePreviewContent(info)) {
    return (
      <p className="text-xs text-muted">已上传文件，但暂无可预览的 Markdown 内容。</p>
    )
  }

  return (
    <ResumeRevealPanel
      visible={visible}
      onToggle={onToggle}
      label={resumeMarkdownToggleLabel(visible)}
    >
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs text-foreground">
        {info.content}
      </pre>
    </ResumeRevealPanel>
  )
}
