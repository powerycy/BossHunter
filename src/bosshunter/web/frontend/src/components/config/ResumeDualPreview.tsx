import { ResumeMarkdownReveal } from '@/components/config/ResumeMarkdownReveal'
import { ResumePdfReveal } from '@/components/config/ResumePdfReveal'
import { hasOriginalPdf, nextResumeContentVisible, type ResumeInfo } from '@/lib/resumeDisplay'
import { useState } from 'react'

type ResumeDualPreviewProps = {
  info: ResumeInfo
}

/** Independent PDF + Markdown preview toggles; both default hidden. */
export function ResumeDualPreview({ info }: ResumeDualPreviewProps) {
  const [pdfVisible, setPdfVisible] = useState(false)
  const [mdVisible, setMdVisible] = useState(false)

  return (
    <div className="mt-3 space-y-2">
      {hasOriginalPdf(info) ? (
        <ResumePdfReveal
          info={info}
          visible={pdfVisible}
          onToggle={() => setPdfVisible(nextResumeContentVisible(pdfVisible))}
        />
      ) : null}
      <ResumeMarkdownReveal
        info={info}
        visible={mdVisible}
        onToggle={() => setMdVisible(nextResumeContentVisible(mdVisible))}
      />
    </div>
  )
}
