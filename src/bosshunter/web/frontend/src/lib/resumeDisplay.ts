/** Pure helpers for resume dual-preview UI. No I/O, no React state. */

export type ResumeInfo = {
  filename: string
  size: number
  size_label?: string
  uploaded_at?: string
  cache_buster?: string
  content?: string
  path: string
  has_original_pdf?: boolean
  original_filename?: string
  original_preview_url?: string
}

/** Flip a visibility flag. */
export function nextResumeContentVisible(isVisible: boolean): boolean {
  return !isVisible
}

/** Label for the converted Markdown preview control. */
export function resumeMarkdownToggleLabel(isVisible: boolean): string {
  return isVisible ? '隐藏转换后的 MD' : '查看转换后的 MD'
}

/** Label for the original PDF preview control. */
export function resumePdfToggleLabel(isVisible: boolean): string {
  return isVisible ? '隐藏原 PDF' : '查看原 PDF'
}

/** Prefer server-provided size_label; fall back to a local KB label. */
export function resumeSizeLabel(info: Pick<ResumeInfo, 'size' | 'size_label'>): string {
  if (info.size_label && info.size_label.trim()) {
    return info.size_label
  }
  return `${(info.size / 1024).toFixed(1)} KB`
}

/** True when the API returned Markdown text we can preview. */
export function hasResumePreviewContent(info: Pick<ResumeInfo, 'content'> | null | undefined): boolean {
  return Boolean(info?.content && info.content.trim())
}

/** True when a companion original PDF is available for preview. */
export function hasOriginalPdf(info: Pick<ResumeInfo, 'has_original_pdf'> | null | undefined): boolean {
  return Boolean(info?.has_original_pdf)
}

/** Build the PDF iframe URL with a second-precision cache buster when available. */
export function resumeOriginalPreviewSrc(
  info: Pick<ResumeInfo, 'original_preview_url' | 'cache_buster' | 'uploaded_at'> | null | undefined,
): string {
  const base = info?.original_preview_url || '/api/resume/original'
  const token = info?.cache_buster || info?.uploaded_at
  if (token) {
    return `${base}?t=${encodeURIComponent(String(token))}`
  }
  return base
}

/** Tip when a file is dropped outside the resume upload zone. Pure. */
export function resumeDropOutsideTip(): string {
  return '请将文件拖放到简历上传区域'
}

/** Two error channels: load/upload vs outside-drop tip. GET must not wipe the tip. */
export type ResumePanelMessages = {
  loadError: string
  dropOutsideTip: string
}

export function emptyResumePanelMessages(): ResumePanelMessages {
  return { loadError: '', dropOutsideTip: '' }
}

/** Prefer load/upload errors; fall back to the outside-drop tip. Pure. */
export function resumePanelVisibleMessage(messages: ResumePanelMessages): string {
  const loadError = String(messages.loadError || '').trim()
  if (loadError) return loadError
  return String(messages.dropOutsideTip || '').trim()
}

/** Successful GET /api/resume clears load errors but keeps an outside-drop tip. Pure. */
export function afterSuccessfulResumeLoad(messages: ResumePanelMessages): ResumePanelMessages {
  return { loadError: '', dropOutsideTip: messages.dropOutsideTip }
}

/** Failed GET /api/resume sets the load error without clearing the tip. Pure. */
export function afterResumeLoadFailure(
  messages: ResumePanelMessages,
  error: string,
): ResumePanelMessages {
  return { loadError: error, dropOutsideTip: messages.dropOutsideTip }
}

/** File dropped outside the upload zone. Pure. */
export function afterDropOutside(messages: ResumePanelMessages, tip: string): ResumePanelMessages {
  return { ...messages, dropOutsideTip: tip }
}

/** Starting an upload/replace attempt clears both channels. Pure. */
export function afterUploadAttempt(messages: ResumePanelMessages): ResumePanelMessages {
  return emptyResumePanelMessages()
}

/** Parse /api/resume failures into a user-visible message. Pure. */
export function resumeLoadErrorMessage(
  statusOk: boolean,
  data: { filename?: string; error?: string } | null | undefined,
): string {
  if (!statusOk) {
    return (data && data.error) || '读取简历失败'
  }
  if (data && data.error) {
    return data.error
  }
  return ''
}

/** True when GET /api/resume path must be written into local config state. Pure. */
export function shouldSyncResumePath(
  currentPath: string | null | undefined,
  serverPath: string | null | undefined,
): boolean {
  const current = String(currentPath || '').trim()
  const server = String(serverPath || '').trim()
  if (!server) return false
  return current !== server
}
