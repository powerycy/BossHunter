import type { WorkbenchTask } from '@/hooks/useDashboard'

const GREET_PAUSE_REASON_LABELS: Record<string, string> = {
  auth: 'AI 鉴权失败（API Key 无效或模型无权限）',
  token_quota: 'AI 额度或账户余额不足',
  rate_limit: 'AI 触发限流（请求或 Token 频率限制）',
  context_limit: '请求超过模型上下文限制',
  output_limit: '输出 Token 上限设置不被模型支持',
  network: 'AI 网络连接失败或超时',
  request_failed: 'AI 服务请求失败',
  output_truncated: 'AI 回复被截断（输出 Token 上限）',
  empty_response: 'AI 未返回有效内容',
}

export function greetPauseReasonLabel(reason: unknown): string {
  const raw = typeof reason === 'string' ? reason.trim() : ''
  if (!raw) return ''
  const kindMatch = raw.match(/\(([a-z_]+)(?:\s*,\s*status=\d+)?\)\s*$/)
  const kind = kindMatch?.[1]
  return (kind && GREET_PAUSE_REASON_LABELS[kind]) || raw
}

export function describeGreetTaskOutcome(task: WorkbenchTask): string {
  const metrics = task.metrics ?? {}
  const conflictCount = task.progress?.conflict_ids?.length ?? 0
  const base = `招呼语生成完成：新生成 ${metrics.greet_generated ?? 0}，保留现有 ${metrics.greet_preserved ?? 0}，失败 ${metrics.greet_failed ?? 0}`
    + (conflictCount ? `；${conflictCount} 个岗位状态已变更未保存` : '')
  if (metrics.greet_paused) {
    const label = greetPauseReasonLabel(metrics.greet_pause_reason) || 'AI 服务异常'
    return `${base}；因${label}提前暂停，已生成内容已保存，剩余岗位下次可继续`
  }
  return base
}
