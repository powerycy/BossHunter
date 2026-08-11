import { useEffect, useState } from 'react'
import type { Job } from '@/hooks/useDashboard'

export interface JobFilters {
  query: string
  minScore: string
  salaryMin: string
  salaryMax: string
  status: string
  hrActivity: string
  createdWithin: string
}

export const EMPTY_JOB_FILTERS: JobFilters = {
  query: '',
  minScore: '',
  salaryMin: '',
  salaryMax: '',
  status: '',
  hrActivity: '',
  createdWithin: '',
}

export function useDebouncedValue<T>(value: T, delay: number) {
  const [debouncedValue, setDebouncedValue] = useState(value)

  useEffect(() => {
    const timeout = window.setTimeout(() => setDebouncedValue(value), delay)
    return () => window.clearTimeout(timeout)
  }, [value, delay])

  return debouncedValue
}

export function hasInvalidSalaryRange(filters: JobFilters) {
  if (filters.salaryMin === '' || filters.salaryMax === '') return false
  return Number(filters.salaryMin) > Number(filters.salaryMax)
}

export function hasActiveJobFilters(filters: JobFilters) {
  return Object.values(filters).some(value => value !== '')
}

function parseMonthlySalaryK(salary: string): [number, number] | null {
  const range = salary.match(/(\d+(?:\.\d+)?)\s*[kK]?\s*-\s*(\d+(?:\.\d+)?)\s*[kK]/)
  if (range) {
    const low = Number(range[1])
    const high = Number(range[2])
    return [Math.min(low, high), Math.max(low, high)]
  }
  const single = salary.match(/(\d+(?:\.\d+)?)\s*[kK](?!\w)/)
  if (single) {
    const value = Number(single[1])
    return [value, value]
  }
  return null
}

export function classifyHrActivity(activity: string) {
  const normalized = (activity || '').trim()
  if (!normalized) return 'unknown'
  if (['在线', '刚刚', '今日', '昨日'].some(keyword => normalized.includes(keyword))) return 'recent_3d'

  const dayMatch = normalized.match(/(\d+)\s*日内活跃/)
  if (dayMatch) {
    const days = Number(dayMatch[1])
    if (days <= 3) return 'recent_3d'
    if (days <= 7) return 'week'
    if (days <= 31) return 'month'
    return 'older'
  }
  if (normalized.includes('本周活跃')) return 'week'

  const weekMatch = normalized.match(/(\d+)\s*周内活跃/)
  if (weekMatch) return Number(weekMatch[1]) <= 1 ? 'week' : 'month'
  if (normalized.includes('本月活跃')) return 'month'
  if (/\d+\s*月内活跃/.test(normalized) || normalized.includes('年前活跃') || normalized.includes('半年前活跃')) return 'older'
  return 'unknown'
}

function parseCreatedAt(createdAt: string) {
  const value = (createdAt || '').trim()
  if (!value) return null
  const normalized = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(value)
    ? `${value.replace(' ', 'T')}Z`
    : value
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

function matchesCreatedWithin(createdAt: string, createdWithin: string) {
  if (!createdWithin) return true
  const created = parseCreatedAt(createdAt)
  if (!created) return false
  const now = new Date()
  if (createdWithin === 'today') {
    return created.getFullYear() === now.getFullYear()
      && created.getMonth() === now.getMonth()
      && created.getDate() === now.getDate()
  }
  const days = createdWithin === '3d' ? 3 : 7
  return now.getTime() - created.getTime() <= days * 24 * 60 * 60 * 1000
}

export function filterJobs(jobs: Job[], filters: JobFilters) {
  if (hasInvalidSalaryRange(filters)) return []
  const keyword = filters.query.trim().toLocaleLowerCase()
  const minimumScore = filters.minScore === '' ? null : Number(filters.minScore)
  const salaryMin = filters.salaryMin === '' ? null : Number(filters.salaryMin)
  const salaryMax = filters.salaryMax === '' ? null : Number(filters.salaryMax)
  const salaryEnabled = salaryMin !== null || salaryMax !== null

  return jobs.filter(job => {
    if (!matchesCreatedWithin(job.created_at, filters.createdWithin)) return false
    if (keyword) {
      const searchable = [job.title, job.company, job.jd, job.score_reason]
        .join('\n')
        .toLocaleLowerCase()
      if (!searchable.includes(keyword)) return false
    }
    if (minimumScore !== null && Number(job.score || 0) < minimumScore) return false
    if (filters.status && job.status !== filters.status) return false
    if (filters.hrActivity && classifyHrActivity(job.hr_active || '') !== filters.hrActivity) return false
    if (salaryEnabled) {
      const salaryRange = parseMonthlySalaryK(job.salary || '')
      if (!salaryRange) return false
      const [jobMin, jobMax] = salaryRange
      if (salaryMin !== null && jobMax < salaryMin) return false
      if (salaryMax !== null && jobMin > salaryMax) return false
    }
    return true
  })
}
