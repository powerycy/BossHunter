/**
 * Runnable unit tests for pure resumeDisplay helpers.
 * Execute: node --experimental-strip-types tests/js/resume_display_helpers.test.ts
 */
import assert from 'node:assert/strict'
import {
  afterDropOutside,
  afterResumeLoadFailure,
  afterSuccessfulResumeLoad,
  afterUploadAttempt,
  emptyResumePanelMessages,
  hasOriginalPdf,
  hasResumePreviewContent,
  nextResumeContentVisible,
  resumeDropOutsideTip,
  resumeLoadErrorMessage,
  resumeMarkdownToggleLabel,
  resumeOriginalPreviewSrc,
  resumePanelVisibleMessage,
  resumePdfToggleLabel,
  resumeSizeLabel,
  shouldSyncResumePath,
} from '../../src/bosshunter/web/frontend/src/lib/resumeDisplay.ts'

assert.equal(nextResumeContentVisible(false), true)
assert.equal(nextResumeContentVisible(true), false)

assert.equal(resumeMarkdownToggleLabel(false), '查看转换后的 MD')
assert.equal(resumeMarkdownToggleLabel(true), '隐藏转换后的 MD')
assert.equal(resumePdfToggleLabel(false), '查看原 PDF')
assert.equal(resumePdfToggleLabel(true), '隐藏原 PDF')

assert.equal(resumeSizeLabel({ size: 2048, size_label: '2.0 KB' }), '2.0 KB')
assert.equal(resumeSizeLabel({ size: 2048 }), '2.0 KB')

assert.equal(hasResumePreviewContent({ content: '# 简历' }), true)
assert.equal(hasResumePreviewContent({ content: '   ' }), false)
assert.equal(hasResumePreviewContent(null), false)

assert.equal(hasOriginalPdf({ has_original_pdf: true }), true)
assert.equal(hasOriginalPdf({ has_original_pdf: false }), false)
assert.equal(hasOriginalPdf(null), false)

assert.equal(
  resumeOriginalPreviewSrc({ original_preview_url: '/api/resume/original', cache_buster: '1725445801' }),
  '/api/resume/original?t=1725445801',
)
assert.equal(
  resumeOriginalPreviewSrc({ original_preview_url: '/api/resume/original', uploaded_at: '2026-09-04 15:30' }),
  '/api/resume/original?t=2026-09-04%2015%3A30',
)
assert.equal(resumeOriginalPreviewSrc({}), '/api/resume/original')

assert.equal(resumeLoadErrorMessage(true, null), '')
assert.equal(resumeLoadErrorMessage(true, { filename: 'a.md' }), '')
assert.equal(resumeLoadErrorMessage(false, { error: '配置的简历文件不存在或无法读取' }), '配置的简历文件不存在或无法读取')
assert.equal(resumeLoadErrorMessage(false, null), '读取简历失败')
assert.equal(resumeLoadErrorMessage(true, { error: 'boom' }), 'boom')

assert.equal(resumeDropOutsideTip(), '请将文件拖放到简历上传区域')

// Outside-drop tip must survive a successful GET /api/resume (concurrent load race).
{
  const withTip = afterDropOutside(emptyResumePanelMessages(), resumeDropOutsideTip())
  assert.equal(resumePanelVisibleMessage(withTip), resumeDropOutsideTip())

  const afterLoad = afterSuccessfulResumeLoad(withTip)
  assert.equal(afterLoad.loadError, '')
  assert.equal(afterLoad.dropOutsideTip, resumeDropOutsideTip())
  assert.equal(resumePanelVisibleMessage(afterLoad), resumeDropOutsideTip())

  const afterFail = afterResumeLoadFailure(withTip, '读取简历失败')
  assert.equal(resumePanelVisibleMessage(afterFail), '读取简历失败')
  assert.equal(afterFail.dropOutsideTip, resumeDropOutsideTip())

  const afterUpload = afterUploadAttempt(withTip)
  assert.deepEqual(afterUpload, emptyResumePanelMessages())
  assert.equal(resumePanelVisibleMessage(afterUpload), '')
}

// Abnormal message-channel cases
{
  assert.equal(resumePanelVisibleMessage(emptyResumePanelMessages()), '')
  assert.equal(
    resumePanelVisibleMessage({ loadError: '   ', dropOutsideTip: resumeDropOutsideTip() }),
    resumeDropOutsideTip(),
  )
  assert.equal(
    resumePanelVisibleMessage({ loadError: '上传失败', dropOutsideTip: resumeDropOutsideTip() }),
    '上传失败',
  )
  // Failed load after a tip still prefers the load error for display.
  const tipped = afterDropOutside(emptyResumePanelMessages(), resumeDropOutsideTip())
  const failed = afterResumeLoadFailure(tipped, '配置的简历文件不存在或无法读取')
  assert.equal(resumePanelVisibleMessage(failed), '配置的简历文件不存在或无法读取')
  // Successful load after failure clears loadError but keeps tip.
  const recovered = afterSuccessfulResumeLoad(failed)
  assert.equal(recovered.loadError, '')
  assert.equal(resumePanelVisibleMessage(recovered), resumeDropOutsideTip())
}

assert.equal(shouldSyncResumePath('/tmp/resume.md', '/tmp/resume.md'), false)
assert.equal(shouldSyncResumePath('/tmp/resume.pdf', '/tmp/resume.md'), true)
assert.equal(shouldSyncResumePath('', '/tmp/resume.md'), true)
assert.equal(shouldSyncResumePath('/tmp/resume.md', ''), false)
assert.equal(shouldSyncResumePath(null, null), false)

console.log('resume_display_helpers: ok')
