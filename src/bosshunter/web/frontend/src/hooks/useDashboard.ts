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
}

interface TopCompany {
  company: string
  avg_score: number
  job_count: number
}

interface HistoryItem {
  action: string
  detail: string
  created_at: string
  company: string
  title: string
}

export function useDashboard() {
  const [funnel, setFunnel] = useState<FunnelData>({})
  const [activity, setActivity] = useState<ActivityData[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [topCompanies, setTopCompanies] = useState<TopCompany[]>([])
  const [history, setHistory] = useState<HistoryItem[]>([])
  const [loading, setLoading] = useState(true)

  const fetchAll = async () => {
    try {
      const [funnelRes, activityRes, jobsRes, companiesRes, historyRes] = await Promise.all([
        fetch('/api/funnel'),
        fetch('/api/activity?days=7'),
        fetch('/api/jobs?limit=100'),
        fetch('/api/top-companies?limit=5'),
        fetch('/api/history?limit=15'),
      ])

      setFunnel(await funnelRes.json())
      setActivity(await activityRes.json())
      setJobs(await jobsRes.json())
      setTopCompanies(await companiesRes.json())
      setHistory(await historyRes.json())
    } catch (err) {
      console.error('Failed to fetch dashboard data:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchAll, 30000)
    return () => clearInterval(interval)
  }, [])

  return { funnel, activity, jobs, topCompanies, history, loading, refresh: fetchAll }
}

export type { FunnelData, ActivityData, Job, TopCompany, HistoryItem }
