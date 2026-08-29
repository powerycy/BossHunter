import { useCallback, useState, useEffect, useRef } from 'react'

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
  source_platform?: 'boss' | 'zhilian' | string
  source_job_id?: string | null
  source_keyword?: string | null
  source_city_code?: string | null
  title: string
  company: string
  salary: string
  city: string
  experience: string
  education?: string
  recruitment_type?: 'campus' | 'experienced' | 'unknown' | string
  jd: string
  score: number
  score_reason: string
  greeting: string
  status: string
  hr_name: string
  hr_title: string
  hr_active: string
  company_size: string
  company_industry: string
  url: string
  created_at: string
  updated_at?: string
  deleted_at?: string | null
  deleted_reason?: string | null
  resume_path?: string
  last_error?: string
}

interface TopCompany {
  company: string
  avg_score: number
  job_count: number
}

export interface WorkbenchTask {
  id: string
  mode: 'full' | 'collect' | 'rescore' | 'monitor' | 'deliver'
  label: string
  status: string
  logs: string[]
  error?: string
  deadline_at?: string
  stop_reason?: string
  stop_requested: boolean
  metrics?: Record<string, number>
  progress?: CollectionProgress
}

export interface CollectionPlatformProgress {
  status: string
  new: number
  target: number | null
  percent?: number | null
  seen?: number
  duplicate?: number
  filtered?: number
  parse_failed?: number
  save_failed?: number
  keyword?: string
  city?: string
  page?: number
  max_pages?: number
  phase?: string
  reason_code?: string
  message?: string
}

export interface CollectionProgress {
  run_id?: string
  outcome?: string
  current_platform?: string
  platform_index?: number
  platform_total?: number
  platforms?: Record<string, CollectionPlatformProgress>
  collected_job_ids?: string[]
}

interface WorkbenchData {
  funnel: FunnelData
  funnel_today: FunnelData
  pending_confirmation: Job[]
  pending_greetings: Job[]
  send_errors: Job[]
  needs_resume: Job[]
  send_quota: { daily_limit: number; sent: number; remaining: number; exhausted: boolean }
  task: WorkbenchTask | null
  last_task: WorkbenchTask | null
}

interface HistoryDetailPayload {
  schema: string
  hr_question?: string
  ai_reply?: string
  pending_hr_question?: string
  pending_history_id?: number
  manual_reply?: string
  system_reason?: string
  conversation_tail?: Array<{
    sender: string
    text: string
    time?: string
    kind?: string
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
  url?: string
  source_platform?: string
  resume_path?: string
  resolved?: boolean
}

const emptyWorkbench: WorkbenchData = {
  funnel: {},
  funnel_today: {},
  pending_confirmation: [],
  pending_greetings: [],
  send_errors: [],
  needs_resume: [],
  send_quota: { daily_limit: 30, sent: 0, remaining: 30, exhausted: false },
  task: null,
  last_task: null,
}

type DashboardDataScope = 'workbench' | 'jobs' | 'monitor' | 'all'

export function useDashboard(scope: DashboardDataScope = 'all') {
  const [workbench, setWorkbench] = useState<WorkbenchData>(emptyWorkbench)
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [lastRefreshedAt, setLastRefreshedAt] = useState<Date | null>(null)
  const refreshingRef = useRef(false)

  const fetchAll = useCallback(async () => {
    if (refreshingRef.current) return
    refreshingRef.current = true
    setRefreshing(true)
    try {
      const needsWorkbench = scope === 'all' || scope === 'workbench'
      const needsHistory = scope === 'all' || scope === 'monitor'
      const fetchOptions = { cache: 'no-store' as const }
      const [workbenchRes, historyRes] = await Promise.all([
        needsWorkbench ? fetch('/api/workbench', fetchOptions) : Promise.resolve(null),
        needsHistory ? fetch('/api/history?limit=50&include_unresolved=1', fetchOptions) : Promise.resolve(null),
      ])
      const [workbenchData, historyData] = await Promise.all([
        workbenchRes ? workbenchRes.json() : Promise.resolve(undefined),
        historyRes ? historyRes.json() : Promise.resolve(undefined),
      ])

      if (workbenchData !== undefined) setWorkbench(workbenchData)
      if (historyData !== undefined) setHistory(historyData)
      setLastRefreshedAt(new Date())
      setError('')
    } catch (err) {
      console.error('Failed to fetch dashboard data:', err)
      setError('无法读取本地控制台数据')
    } finally {
      refreshingRef.current = false
      setRefreshing(false)
      setLoading(false)
    }
  }, [scope])

  const startTask = async (mode: 'full' | 'collect' | 'rescore' | 'monitor' | 'deliver', options?: Record<string, unknown>) => {
    const res = await fetch('/api/workbench/task', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode, ...(options ? { options } : {}) }),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      const details = Array.isArray(data.messages) ? data.messages.map(String).filter(Boolean).join('；') : ''
      throw new Error([data.error || '启动失败', details].filter(Boolean).join('：'))
    }
    const task = await res.json() as WorkbenchTask
    setWorkbench(prev => ({ ...prev, task, last_task: task }))
    void fetchAll()
    return task
  }

  const stopTask = async (taskId: string) => {
    const res = await fetch(`/api/workbench/task/${taskId}/stop`, { method: 'POST' })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      throw new Error(data.error || '停止失败')
    }
    const task = await res.json() as WorkbenchTask
    setWorkbench(prev => ({ ...prev, task, last_task: task }))
    void fetchAll()
    return task
  }

  useEffect(() => {
    void fetchAll()
    const pollIntervalMs = scope === 'monitor' ? 2000 : 5000
    const refreshWhenVisible = () => {
      if (document.visibilityState === 'visible') void fetchAll()
    }
    const interval = window.setInterval(refreshWhenVisible, pollIntervalMs)
    window.addEventListener('focus', refreshWhenVisible)
    document.addEventListener('visibilitychange', refreshWhenVisible)
    return () => {
      window.clearInterval(interval)
      window.removeEventListener('focus', refreshWhenVisible)
      document.removeEventListener('visibilitychange', refreshWhenVisible)
    }
  }, [fetchAll, scope])

  return {
    workbench,
    history,
    loading,
    error,
    refreshing,
    lastRefreshedAt,
    refresh: fetchAll,
    startTask,
    stopTask,
  }
}

export type { FunnelData, ActivityData, Job, TopCompany, WorkbenchData, HistoryDetailPayload, HistoryItem }
