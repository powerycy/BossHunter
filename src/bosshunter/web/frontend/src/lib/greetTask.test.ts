import { describe, expect, it } from 'vitest'
import { describeGreetTaskOutcome, greetPauseReasonLabel } from './greetTask'
import type { WorkbenchTask } from '@/hooks/useDashboard'

function buildTask(overrides: Partial<WorkbenchTask>): WorkbenchTask {
  return {
    id: 'task-1',
    mode: 'greet',
    label: '生成招呼语',
    status: 'completed',
    logs: [],
    stop_requested: false,
    ...overrides,
  }
}

describe('greetPauseReasonLabel', () => {
  it('maps known pause reason kinds to friendly labels', () => {
    expect(greetPauseReasonLabel('AI Token 额度或账户余额不足 (token_quota, status=402)')).toBe('AI 额度或账户余额不足')
    expect(greetPauseReasonLabel('AI API Key 无效或当前模型没有访问权限 (auth, status=401)')).toBe('AI 鉴权失败（API Key 无效或模型无权限）')
    expect(greetPauseReasonLabel('AI 服务触发请求或 Token 频率限制 (rate_limit, status=429)')).toBe('AI 触发限流（请求或 Token 频率限制）')
    expect(greetPauseReasonLabel('AI 服务连接失败或超时 (network)')).toBe('AI 网络连接失败或超时')
    expect(greetPauseReasonLabel('请求内容超过当前模型的上下文限制 (context_limit)')).toBe('请求超过模型上下文限制')
  })

  it('falls back to the raw reason for unknown kinds', () => {
    expect(greetPauseReasonLabel('某个未知服务异常 (new_kind, status=500)')).toBe('某个未知服务异常 (new_kind, status=500)')
    expect(greetPauseReasonLabel('没有 kind 后缀的原始错误')).toBe('没有 kind 后缀的原始错误')
  })

  it('returns empty string for missing reasons', () => {
    expect(greetPauseReasonLabel(undefined)).toBe('')
    expect(greetPauseReasonLabel('')).toBe('')
    expect(greetPauseReasonLabel(42)).toBe('')
  })
})

describe('describeGreetTaskOutcome', () => {
  it('summarizes a fully completed greet task', () => {
    const task = buildTask({
      metrics: { greet_generated: 5, greet_preserved: 2, greet_failed: 1 },
    })
    expect(describeGreetTaskOutcome(task)).toBe('招呼语生成完成：新生成 5，保留现有 2，失败 1')
  })

  it('appends conflict count when jobs changed state', () => {
    const task = buildTask({
      metrics: { greet_generated: 3, greet_preserved: 0, greet_failed: 0 },
      progress: { conflict_ids: ['job-1', 'job-2'] },
    })
    expect(describeGreetTaskOutcome(task)).toBe('招呼语生成完成：新生成 3，保留现有 0，失败 0；2 个岗位状态已变更未保存')
  })

  it('explains the pause reason for partial success', () => {
    const task = buildTask({
      metrics: {
        greet_generated: 3,
        greet_preserved: 0,
        greet_failed: 0,
        greet_paused: 1,
        greet_pause_reason: 'AI Token 额度或账户余额不足 (token_quota, status=402)',
      },
    })
    expect(describeGreetTaskOutcome(task)).toBe(
      '招呼语生成完成：新生成 3，保留现有 0，失败 0；因AI 额度或账户余额不足提前暂停，已生成内容已保存，剩余岗位下次可继续'
    )
  })

  it('uses a generic fallback when the pause reason is missing', () => {
    const task = buildTask({
      metrics: { greet_generated: 1, greet_preserved: 0, greet_failed: 0, greet_paused: 1 },
    })
    expect(describeGreetTaskOutcome(task)).toBe(
      '招呼语生成完成：新生成 1，保留现有 0，失败 0；因AI 服务异常提前暂停，已生成内容已保存，剩余岗位下次可继续'
    )
  })
})
