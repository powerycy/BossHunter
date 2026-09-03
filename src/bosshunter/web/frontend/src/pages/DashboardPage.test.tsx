import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import DashboardPage from './DashboardPage'
import type { WorkbenchTask } from '@/hooks/useDashboard'

let workbenchPayload: Record<string, unknown> = {}
let stopResponse: () => Response = () => jsonResponse({})

function jsonResponse(body: unknown, ok = true) {
  return new Response(JSON.stringify(body), {
    status: ok ? 200 : 500,
    headers: { 'Content-Type': 'application/json' },
  })
}

function buildTask(overrides: Partial<WorkbenchTask>): WorkbenchTask {
  return {
    id: 'task-1',
    mode: 'greet',
    label: '生成招呼语',
    status: 'running',
    logs: [],
    stop_requested: false,
    ...overrides,
  }
}

function baseWorkbench(overrides: Record<string, unknown> = {}) {
  return {
    funnel: {},
    funnel_today: {},
    pending_confirmation: [],
    pending_greetings: [],
    send_errors: [],
    needs_resume: [],
    send_quota: { daily_limit: 30, sent: 0, remaining: 30, exhausted: false },
    task: null,
    last_task: null,
    ...overrides,
  }
}

const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input)
  if (url === '/api/workbench' && (!init?.method || init.method === 'GET')) {
    return jsonResponse(workbenchPayload)
  }
  if (url.includes('/stop') && init?.method === 'POST') {
    return stopResponse()
  }
  return jsonResponse({})
})

describe('DashboardPage workbench task panel', () => {
  beforeEach(() => {
    workbenchPayload = baseWorkbench()
    stopResponse = () => jsonResponse({})
    vi.stubGlobal('fetch', fetchMock)
    fetchMock.mockClear()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('shows per-job greet progress from task logs', async () => {
    workbenchPayload = baseWorkbench({
      task: buildTask({
        status: 'running',
        logs: ['开始为 3 个岗位生成招呼语', '生成招呼语 (2/3)：字节跳动｜后端工程师'],
      }),
    })
    render(<DashboardPage view="workbench" />)
    expect(await screen.findByText('生成招呼语 (2/3)：字节跳动｜后端工程师')).toBeTruthy()
  })

  it('shows the pause reason when a greet task completed with partial success', async () => {
    workbenchPayload = baseWorkbench({
      last_task: buildTask({
        status: 'completed',
        metrics: {
          greet_generated: 3,
          greet_preserved: 1,
          greet_failed: 0,
          greet_paused: 1,
          greet_pause_reason: 'AI Token 额度或账户余额不足 (token_quota, status=402)',
        },
      }),
    })
    render(<DashboardPage view="workbench" />)
    expect(await screen.findByText('提前暂停原因：AI 额度或账户余额不足。已生成内容已保存，剩余岗位下次运行会继续处理。')).toBeTruthy()
    expect(screen.getByText('提前暂停')).toBeTruthy()
  })

  it('shows failure feedback and the raw error for a zero-output greet task', async () => {
    workbenchPayload = baseWorkbench({
      last_task: buildTask({
        status: 'failed',
        error: '招呼语生成已安全暂停：AI 服务触发请求或 Token 频率限制 (rate_limit, status=429)',
      }),
    })
    const { container } = render(<DashboardPage view="workbench" />)
    expect(await screen.findByText('任务运行失败')).toBeTruthy()
    expect(container.textContent).toContain('招呼语生成已安全暂停：AI 服务触发请求或 Token 频率限制 (rate_limit, status=429)')
  })

  it('recovers the notice and surfaces the real error when stopping fails', async () => {
    workbenchPayload = baseWorkbench({
      task: buildTask({ mode: 'collect', label: '单独采集', status: 'running' }),
    })
    stopResponse = () => jsonResponse({ error: '任务已结束，无法停止' }, false)
    const confirmSpy = vi.fn(() => true)
    vi.stubGlobal('confirm', confirmSpy)
    render(<DashboardPage view="workbench" />)
    const stopButton = await screen.findByRole('button', { name: '停止任务' })
    fireEvent.click(stopButton)
    expect(confirmSpy).toHaveBeenCalled()
    await waitFor(() => {
      expect(screen.getByText('单独采集停止失败：任务已结束，无法停止')).toBeTruthy()
    })
  })

  it('confirms the stop request when the backend accepts it', async () => {
    workbenchPayload = baseWorkbench({
      task: buildTask({ mode: 'collect', label: '单独采集', status: 'running' }),
    })
    stopResponse = () => jsonResponse(buildTask({ mode: 'collect', label: '单独采集', status: 'stopping', stop_requested: true }))
    vi.stubGlobal('confirm', () => true)
    render(<DashboardPage view="workbench" />)
    const stopButton = await screen.findByRole('button', { name: '停止任务' })
    fireEvent.click(stopButton)
    await waitFor(() => {
      expect(screen.getByText('单独采集已请求停止。')).toBeTruthy()
    })
  })
})
