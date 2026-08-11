import { useState, useEffect } from 'react'

interface FunnelData {
  [key: string]: number
}

interface ActivityData {
  day: string
  action: string
  cnt: number
}

interface Job {
  id: string
  title: string
  company: string
  salary: string
  city: string
  city_code?: string
  experience: string
  jd: string
  score: number
  score_reason: string
  greeting: string
  status: string
  hr_name: string
  hr_title: string
  company_size: string
  company_industry: string
  url: string
  created_at: string
  updated_at?: string
  resume_path?: string
  last_error?: string
  score_failure_json?: string
  greeting_status?: string
  greeting_failure_json?: string
  greeting_attempts?: number
  greeting_updated_at?: string
  filter_source?: string
  filter_reason?: string
  manual_override?: number
  deleted_at?: string | null
  deleted_reason?: string | null
  permanent_delete_allowed?: boolean
  permanent_delete_reasons?: string[]
}

interface TopCompany {
  company: string
  avg_score: number
  job_count: number
}

interface WorkbenchTask {
  id: string
  mode: 'full' | 'collect' | 'score' | 'rescore' | 'greet' | 'monitor' | 'deliver'
  label: string
  status: string
  logs: string[]
  error?: string
  deadline_at?: string
  stop_reason?: string
  stop_requested: boolean
  pause_requested?: boolean
  stage?: string
  progress?: Record<string, unknown>
  can_pause?: boolean
  can_resume?: boolean
  recoverable?: boolean
  checkpoint_summary?: Record<string, unknown>
}

interface WorkbenchData {
  funnel: FunnelData
  pending_confirmation: Job[]
  pending_greetings: Job[]
  greeting_generation_items?: Job[]
  ready_to_send?: Job[]
  send_errors: Job[]
  needs_resume: Job[]
  task: WorkbenchTask | null
  last_task: WorkbenchTask | null
}

interface HistoryDetailPayload {
  schema: string
  hr_question: string
  ai_reply: string
  system_reason?: string
  conversation_tail?: Array<{
    sender: string
    text: string
  }>
}

interface HistoryItem {
  id: number
  job_id: string
  action: string
  detail: string
  detail_payload?: HistoryDetailPayload
  created_at: string
  company: string
  title: string
  resume_path?: string
  resolved?: boolean
}

const emptyWorkbench: WorkbenchData = {
  funnel: {},
  pending_confirmation: [],
  pending_greetings: [],
  send_errors: [],
  needs_resume: [],
  task: null,
  last_task: null,
  greeting_generation_items: [],
  ready_to_send: [],
}

type DashboardDataScope = 'workbench' | 'jobs' | 'monitor' | 'all'

export function useDashboard(scope: DashboardDataScope = 'all') {
  const [workbench, setWorkbench] = useState<WorkbenchData>(emptyWorkbench)
  const [jobs, setJobs] = useState<Job[]>([])
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const fetchAll = async () => {
    try {
      const needsWorkbench = scope === 'all' || scope === 'workbench'
      const needsJobs = scope === 'all' || scope === 'jobs'
      const needsHistory = scope === 'all' || scope === 'monitor'
      const fetchOptions = { cache: 'no-store' as const }
      const fetchAllJobs = async () => {
        const pageSize = 200
        const collected: Job[] = []
        let offset = 0
        let total: number | null = null
        while (true) {
          const pageRes = await fetch(`/api/jobs?limit=${pageSize}&offset=${offset}`, fetchOptions)
          if (!pageRes.ok) throw new Error(`岗位接口返回 ${pageRes.status}`)
          const page = await pageRes.json()
          if (!Array.isArray(page)) throw new Error('岗位接口返回格式无效')
          collected.push(...page)
          const headerTotal = Number(pageRes.headers.get('X-Total-Count'))
          if (Number.isFinite(headerTotal)) total = headerTotal
          if (!page.length || (total !== null && collected.length >= total) || page.length < pageSize) break
          offset += page.length
        }
        const unique = new Map<string, Job>()
        collected.forEach(job => unique.set(String(job.id), job))
        return [...unique.values()]
      }
      const [workbenchRes, jobsData, historyRes] = await Promise.all([
        needsWorkbench ? fetch('/api/workbench', fetchOptions) : Promise.resolve(null),
        needsJobs ? fetchAllJobs() : Promise.resolve(undefined),
        needsHistory ? fetch('/api/history?limit=50&include_unresolved=1', fetchOptions) : Promise.resolve(null),
      ])
      const [workbenchData, historyData] = await Promise.all([
        workbenchRes ? workbenchRes.json() : Promise.resolve(undefined),
        historyRes ? historyRes.json() : Promise.resolve(undefined),
      ])

      if (workbenchData !== undefined) setWorkbench(workbenchData)
      if (jobsData !== undefined) setJobs(jobsData)
      if (historyData !== undefined) setHistory(historyData)
      setError('')
    } catch (err) {
      console.error('Failed to fetch dashboard data:', err)
      setError('无法读取本地控制台数据')
    } finally {
      setLoading(false)
    }
  }

  const startTask = async (
    mode: 'full' | 'collect' | 'score' | 'rescore' | 'greet' | 'monitor' | 'deliver',
    options?: Record<string, unknown>,
  ) => {
    const res = await fetch('/api/workbench/task', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode, ...(options ? { options } : {}) }),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      const details = Array.isArray(data.messages) ? data.messages.map(String).filter(Boolean).join('；') : ''
      const conflict = data.conflict?.choices?.map((choice: { label?: string }) => choice.label).filter(Boolean).join(' / ')
      throw new Error([data.error || '启动失败', details, conflict ? `可选：${conflict}` : ''].filter(Boolean).join('：'))
    }
    await fetchAll()
  }

  const stopTask = async (taskId: string) => {
    const res = await fetch(`/api/workbench/task/${taskId}/stop`, { method: 'POST' })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.error || '停止失败')
    }
    await fetchAll()
  }

  const pauseTask = async (taskId: string) => {
    const res = await fetch(`/api/workbench/task/${taskId}/pause`, { method: 'POST' })
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || '暂停失败')
    await fetchAll()
  }

  const resumeTask = async (taskId: string) => {
    const res = await fetch(`/api/workbench/task/${taskId}/resume`, { method: 'POST' })
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || '恢复失败')
    await fetchAll()
  }

  const endTask = async (taskId: string) => {
    const res = await fetch(`/api/workbench/task/${taskId}/end`, { method: 'POST' })
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || '结束失败')
    await fetchAll()
  }

  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchAll, 5000)
    return () => clearInterval(interval)
  }, [])

  return { workbench, jobs, history, loading, error, refresh: fetchAll, startTask, stopTask, pauseTask, resumeTask, endTask }
}

export type { FunnelData, ActivityData, Job, TopCompany, WorkbenchData, WorkbenchTask, HistoryDetailPayload, HistoryItem }
