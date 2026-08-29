export const STATUS_LABELS: Record<string, string> = {
  pending: '待评分',
  scored: '已评分',
  filtered: '已过滤',
  ready: '待确认',
  approved: '已确认',
  skipped: '已跳过',
  sent: '已发送',
  replied: '已回复',
  resume_sent: '简历已发',
  needs_resume: '待手动发简历',
  follow_up_sent: '已跟进',
  rejected: '已拒绝',
  error: '发送失败',
}

export const ACTION_LABELS: Record<string, string> = {
  scrape: '采集',
  scored: '评分',
  filtered: '过滤',
  ready: '待确认',
  approved: '确认',
  skipped: '跳过',
  sent: '发送',
  manual_sent: '手动标记已发送',
  replied: '回复',
  hr_reply_detected: 'HR 有新消息',
  reply_pending: '待确认回复',
  reply_dismissed: '已放弃回复',
  auto_replied: '自动回复',
  resume_failed: '简历生成失败',
  resume_sent: '简历已发',
  needs_resume: '待手动发简历',
  follow_up_sent: '已跟进',
  rejected: '拒绝',
  error: '错误',
}

export function getStatusLabel(status: string | null | undefined): string {
  if (!status) return '未知'
  return STATUS_LABELS[status] ?? status
}

export function getActionLabel(action: string | null | undefined): string {
  if (!action) return '未知'
  return ACTION_LABELS[action] ?? action
}
