import { useEffect, useState } from 'react'
import type { Job } from '@/hooks/useDashboard'
import { hasInvalidSalaryRange, useDebouncedValue, type JobFilters } from '@/lib/jobFilters'

interface JobSearchResponse {
  items: Job[]
  total: number
  all_total: number
  limit: number
  offset: number
}

export function useJobSearch(filters: JobFilters, page: number, pageSize: number) {
  const debouncedQuery = useDebouncedValue(filters.query, 250)
  const [items, setItems] = useState<Job[]>([])
  const [total, setTotal] = useState(0)
  const [allTotal, setAllTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    if (hasInvalidSalaryRange(filters)) {
      setItems([])
      setTotal(0)
      setLoading(false)
      setError('')
      return
    }

    const controller = new AbortController()
    const params = new URLSearchParams({
      limit: String(pageSize),
      offset: String(page * pageSize),
    })
    if (debouncedQuery.trim()) params.set('q', debouncedQuery.trim())
    if (filters.minScore) params.set('min_score', filters.minScore)
    if (filters.salaryMin) params.set('salary_min', filters.salaryMin)
    if (filters.salaryMax) params.set('salary_max', filters.salaryMax)
    if (filters.status) params.set('status', filters.status)
    if (filters.hrActivity) params.set('hr_activity', filters.hrActivity)
    if (filters.createdWithin) params.set('created_within', filters.createdWithin)

    setLoading(true)
    fetch(`/api/jobs/search?${params.toString()}`, { cache: 'no-store', signal: controller.signal })
      .then(async response => {
        const data = await response.json()
        if (!response.ok) throw new Error(data.error || '岗位筛选失败')
        return data as JobSearchResponse
      })
      .then(data => {
        setItems(data.items)
        setTotal(data.total)
        setAllTotal(data.all_total)
        setError('')
      })
      .catch(cause => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        setError(cause instanceof Error ? cause.message : '岗位筛选失败')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })

    return () => controller.abort()
  }, [debouncedQuery, filters.minScore, filters.salaryMin, filters.salaryMax, filters.status, filters.hrActivity, filters.createdWithin, page, pageSize])

  return { items, total, allTotal, loading, error }
}
