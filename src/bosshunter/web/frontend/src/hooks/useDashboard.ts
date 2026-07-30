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
}

interface TopCompany {
  company: string
  avg_score: number
  job_count: number
}

interface WorkbenchTask {
  id: string
  mode: 'full' | 'collect' | 'monitor' | 'deliver'
  label: string
  status: string
  logs: string[]
  error?: string
  deadline_at?: string
  stop_reason?: string
  stop_requested: boolean
}

interface WorkbenchData {
  funnel: FunnelData
  pending_confirmation: Job[]
  pending_greetings: Job[]
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
      const [workbenchRes, jobsRes, historyRes] = await Promise.all([
        needsWorkbench ? fetch('/api/workbench', fetchOptions) : Promise.resolve(null),
        needsJobs ? fetch('/api/jobs?limit=100', fetchOptions) : Promise.resolve(null),
        needsHistory ? fetch('/api/history?limit=50&include_unresolved=1', fetchOptions) : Promise.resolve(null),
      ])
      const [workbenchData, jobsData, historyData] = await Promise.all([
        workbenchRes ? workbenchRes.json() : Promise.resolve(undefined),
        jobsRes ? jobsRes.json() : Promise.resolve(undefined),
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

  const startTask = async (mode: 'full' | 'collect' | 'monitor' | 'deliver') => {
    const res = await fetch('/api/workbench/task', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      const details = Array.isArray(data.messages) ? data.messages.map(String).filter(Boolean).join('；') : ''
      throw new Error([data.error || '启动失败', details].filter(Boolean).join('：'))
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

  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchAll, 30000)
    return () => clearInterval(interval)
  }, [])

  return { workbench, jobs, history, loading, error, refresh: fetchAll, startTask, stopTask }
}

export type { FunnelData, ActivityData, Job, TopCompany, WorkbenchData, WorkbenchTask, HistoryDetailPayload, HistoryItem }
