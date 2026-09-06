import { useEffect, useMemo, useState } from 'react'
import { useDashboard, type CollectionProgress, type HistoryItem, type Job, type WorkbenchTask } from '@/hooks/useDashboard'
import { useJobSearch, type JobSortKey, type JobSortOrder } from '@/hooks/useJobSearch'
import { Button } from '@/components/ui/button'
import { JobsTable } from '@/components/dashboard/JobsTable'
import { RecycleBinPanel } from '@/components/dashboard/RecycleBinPanel'
import { ScoreJobsDialog } from '@/components/dashboard/ScoreJobsDialog'
import { CollectJobsDialog } from '@/components/dashboard/CollectJobsDialog'
import { JobFilterBar } from '@/components/jobs/JobFilterBar'
import { parseHistoryDetail } from '@/lib/historyDetail'
import {
  EMPTY_JOB_FILTERS,
  filterJobs,
  hasInvalidSalaryRange,
  useDebouncedValue,
  type JobFilters,
} from '@/lib/jobFilters'
import { getActionLabel, getStatusLabel } from '@/lib/status'
import { cn } from '@/lib/utils'
import {
  AlertTriangle,
  BriefcaseBusiness,
  Download,
  ExternalLink,
  Eye,
  MessageCircle,
  Play,
  RefreshCw,
  ShieldCheck,
  Send,
  Square,
  Trash2,
  XCircle,
} from 'lucide-react'

type WorkbenchMode = 'full' | 'collect' | 'rescore' | 'monitor'
type DashboardView = 'workbench' | 'jobs' | 'monitor'
type StatsScope = 'today' | 'total'

const TASK_STAGE_LABELS = [
  '开始采集岗位',
  '开始 AI 评分',
  '开始重新评分',
  'AI 评分进度',
  '等待前端确认投递',
  '发送失败待处理',
  '执行一轮监测',
  '本轮监测完成，30 分钟后再次检查',
]

function currentTaskStage(task: WorkbenchTask) {
  const logs = task.logs || []
  for (const log of logs.slice().reverse()) {
    if (log.includes('AI 评分进度')) return log
    if (log.includes('招呼语进度')) return log
    if (log.includes('招呼语发送结果')) return log
    if (log.includes('发送招呼语')) return '发送招呼语'
    if (log.includes('生成招呼语')) return '生成招呼语'
    if (log.includes('本轮监测完成')) return log
    const stage = TASK_STAGE_LABELS.find(label => log.includes(label))
    if (stage) return stage
  }
  if (task.status === 'running') return `${task.label}正在启动`
  if (task.status === 'stopping') return `${task.label}正在停止`
  if (task.status === 'completed') return `${task.label}已完成`
  if (task.status === 'stopped') return `${task.label}已停止`
  if (task.status === 'failed') return `${task.label}运行失败`
  return `${task.label}状态未知`
}

function taskStatusText(status: string) {
  if (status === 'failed') return '运行失败'
  if (status === 'completed') return '已结束'
  if (status === 'stopped') return '已停止'
  if (status === 'stopping') return '停止中'
  return '运行中'
}

function taskStatusClass(status: string) {
  if (status === 'failed') return 'border-red-100 bg-red-50'
  if (status === 'completed' || status === 'stopped') return 'border-card-border bg-white'
  return 'border-primary/20 bg-[#FFF0E5]'
}

function taskStatusTitle(status: string) {
  if (status === 'completed' || status === 'stopped') return '最近任务状态'
  return '当前阶段'
}

function taskStopReasonLabel(reason?: string) {
  if (reason === 'daily_limit') return '今日发送额度已用完，岗位已保留在“待发送招呼语”；明日额度恢复后再重试。'
  if (reason === 'outside_window') return '当前不在发送时间窗口内，岗位已保留在“待发送招呼语”。'
  if (reason === 'day_off') return '今日触发防检测休息策略，岗位已保留在“待发送招呼语”。'
  if (reason === 'stopped') return '任务已按你的要求停止，尚未处理的岗位仍保留在队列中。'
  return reason
}

function taskErrorFeedback(error: string) {
  const normalized = error.toLowerCase()
  if (
    normalized.includes('api key')
    || normalized.includes('authentication')
    || normalized.includes('unauthorized')
    || normalized.includes('401')
    || normalized.includes('403')
  ) {
    return {
      title: 'AI 接口认证失败',
      detail: '请到“配置 → AI 设置”检查 API Key、Base URL 和模型名称，保存后点击“测试连接”。',
    }
  }
  if (
    normalized.includes('chrome')
    || normalized.includes('cdp')
    || normalized.includes('websocket')
    || normalized.includes('browser runtime')
    || normalized.includes('not connected')
  ) {
    return {
      title: 'Google Chrome 连接中断',
      detail: '请确认 Google Chrome 正在运行且已开启远程调试，再点击上方“重新检查”。',
    }
  }
  if (normalized.includes('zhipin') || normalized.includes('登录') || normalized.includes('login')) {
    return {
      title: '招聘平台页面或登录状态异常',
      detail: '请在已连接的 Google Chrome 中打开 BOSS 直聘并确认账号仍处于登录状态。',
    }
  }
  return {
    title: '任务运行失败',
    detail: '请查看原始错误；修复配置或连接问题后，重新运行启动检查。',
  }
}

interface DashboardPageProps {
  view?: DashboardView
}

interface PreflightCheck {
  id: string
  title: string
  status: 'pass' | 'warning' | 'error'
  message: string
  detail: string
  action?: 'config' | 'browser' | ''
}

const modes: Array<{ mode: WorkbenchMode; title: string; description: string }> = [
  {
    mode: 'full',
    title: '运行全流程',
    description: '采集 → AI评分 → 确认投递 → 打招呼 → 持续监测，一次跑完整流程。',
  },
  {
    mode: 'collect',
    title: '单独采集',
    description: '打开岗位采集窗口，选择 BOSS/智联/51job、最大页数、排序和执行顺序；默认只采集不评分。',
  },
  {
    mode: 'monitor',
    title: '单独监测',
    description: '只监测过往已投递项目；发现 HR 要简历或问题后进入对应处理。',
  },
]

const statItems = [
  { key: '采集总数', todayLabel: '今日新增岗位', totalLabel: '累计采集岗位' },
  { key: '初筛通过', todayLabel: '今日初筛通过', totalLabel: '累计初筛通过', highlight: true },
  { key: 'AI评分', todayLabel: '今日 AI 评分', totalLabel: '累计 AI 评分' },
  { key: 'pending', todayLabel: '当前待确认', totalLabel: '当前待确认', highlight: true, current: true },
  { key: '发送', todayLabel: '今日已投递', totalLabel: '累计已投递', highlight: true },
]

const taskMetricItems = [
  { key: 'collect_seen', label: '本轮扫描' },
  { key: 'collect_new', label: '本轮新增' },
  { key: 'collect_duplicate', label: '重复岗位' },
  { key: 'collect_filtered', label: '过滤' },
  { key: 'collect_parse_failed', label: '解析失败' },
  { key: 'collect_save_failed', label: '保存失败' },
  { key: 'ai_passed', label: 'AI通过' },
  { key: 'ai_filtered', label: 'AI过滤' },
  { key: 'ai_failed', label: 'AI失败' },
  { key: 'send_success', label: '发送成功' },
  { key: 'send_deferred', label: '待下次发送' },
  { key: 'send_remaining_quota', label: '今日剩余额度' },
]

function jobSubtitle(job: Job) {
  return [job.score ? `匹配 ${job.score}` : '', job.salary, job.hr_active || '活跃度未知', getStatusLabel(job.status)].filter(Boolean).join(' · ')
}

function safeExternalUrl(value: string | undefined, platform: string) {
	if (!value) return ''
	try {
		const url = new URL(value)
		if (url.protocol !== 'https:') return ''
		const allowedDomain = platform === 'zhilian'
			? 'zhaopin.com'
			: platform === '51job'
				? '51job.com'
				: ''
		if (!allowedDomain) return ''
		return url.hostname === allowedDomain || url.hostname.endsWith(`.${allowedDomain}`) ? url.href : ''
	} catch {
		return ''
	}
}

function monitorChatUrl(item: HistoryItem) {
  const platform = item.source_platform || 'boss'
	if (platform === 'boss') return ''
	return safeExternalUrl(item.url, platform)
}

function monitorLinkLabel(item: HistoryItem) {
  return (item.source_platform || 'boss') === 'boss' ? '打开聊天对话' : '打开对应页面'
}

async function parsePreflightResponse(res: Response) {
  const rawText = await res.text()
  let data: { ok?: boolean; messages?: unknown; checks?: unknown; error?: string } = {}
  try {
    data = rawText ? JSON.parse(rawText) : {}
  } catch {
    const message = `无法解析预检响应：预检接口返回 ${res.status}`
    return {
      ok: false,
      messages: [message],
      checks: [{ id: 'preflight_api', title: '启动检查', status: 'error', message, detail: '请重启 BossHunter 后重试。' }] as PreflightCheck[],
    }
  }
  const messages = Array.isArray(data.messages) ? data.messages.map(String).filter(Boolean) : []
  const checks = Array.isArray(data.checks)
    ? data.checks.filter((item): item is PreflightCheck => Boolean(
      item
      && typeof item === 'object'
      && 'id' in item
      && 'status' in item
      && 'message' in item
    ))
    : []
  if (data.error) messages.push(String(data.error))
  if (!res.ok) messages.push(`预检接口返回 ${res.status}`)
  if (!data.ok && messages.length === 0) messages.push('后端未返回具体原因')
  if (checks.length === 0 && messages.length > 0) {
    checks.push(...messages.map((message, index) => ({
      id: `legacy-${index}`,
      title: '启动检查',
      status: 'error' as const,
      message,
      detail: '请按提示修复后重新检测。',
    })))
  }
  return { ok: Boolean(res.ok && data.ok), messages, checks }
}

function PreflightPanel({
  checks,
  checking,
  onRetry,
}: {
  checks: PreflightCheck[]
  checking: boolean
  onRetry: () => void
}) {
  const actionableChecks = checks.filter(check => check.status !== 'pass')
  if (actionableChecks.length === 0) return null

  const errors = actionableChecks.filter(check => check.status === 'error').length
  const warnings = actionableChecks.filter(check => check.status === 'warning').length
  const needsConfig = actionableChecks.some(check => check.action === 'config')
  const heading = errors ? `启动检查发现 ${errors} 个问题` : `启动检查有 ${warnings} 项提醒`

  return (
    <div className={`mt-3 rounded-3xl border p-4 ${
      errors ? 'border-red-200 bg-red-50' : 'border-amber-200 bg-amber-50'
    }`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          {errors
            ? <XCircle className="h-5 w-5 text-danger" />
            : <AlertTriangle className="h-5 w-5 text-amber-600" />}
          <div className="text-sm font-black text-foreground">{heading}</div>
        </div>
        <div className="flex items-center gap-2">
          {needsConfig && (
            <Button variant="secondary" size="sm" onClick={() => window.location.assign('/config')}>
              打开配置
            </Button>
          )}
          <Button variant="secondary" size="sm" onClick={onRetry} disabled={checking}>
            <RefreshCw className={`mr-2 h-4 w-4 ${checking ? 'animate-spin' : ''}`} />
            {checking ? '检查中' : '重新检查'}
          </Button>
        </div>
      </div>
      <div className="mt-3 grid gap-2 lg:grid-cols-2">
        {actionableChecks.map(check => {
          const isError = check.status === 'error'
          return (
            <div
              key={`${check.id}-${check.title}`}
              className={`rounded-2xl border bg-white px-3 py-3 ${isError ? 'border-red-200' : 'border-amber-200'}`}
            >
              <div className="flex items-start gap-2">
                {isError
                  ? <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
                  : <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />}
                <div>
                  <div className="text-xs font-black text-muted">{check.title}</div>
                  <div className="mt-0.5 text-sm font-black text-foreground">{check.message}</div>
                  <p className="mt-1 text-xs leading-5 text-muted">{check.detail}</p>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function DashboardPage({ view = 'workbench' }: DashboardPageProps) {
  const {
    workbench,
    history,
    loading,
    error,
    refreshing,
    lastRefreshedAt,
    refresh,
    startTask,
    stopTask,
  } = useDashboard(view)
  const [selected, setSelected] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  const [preflightChecks, setPreflightChecks] = useState<PreflightCheck[]>([])
  const [preflightMode, setPreflightMode] = useState<WorkbenchMode>('full')
  const [selectedJob, setSelectedJob] = useState<Job | null>(null)
  const [modePending, setModePending] = useState<WorkbenchMode | null>(null)
  const [confirmedDeliveryIds, setConfirmedDeliveryIds] = useState<Set<string>>(new Set())
  const [todayFilters, setTodayFilters] = useState<JobFilters>({ ...EMPTY_JOB_FILTERS })
  const [statsScope, setStatsScope] = useState<StatsScope>('today')
  const [collectDialogOpen, setCollectDialogOpen] = useState(false)
  const [collectDialogMode, setCollectDialogMode] = useState<'collect' | 'full'>('collect')
  const [preflightRunning, setPreflightRunning] = useState(false)

  const todayJobs = useMemo(
    () => workbench.pending_confirmation.filter(job => !confirmedDeliveryIds.has(job.id)),
    [workbench.pending_confirmation, confirmedDeliveryIds]
  )
  const debouncedTodayQuery = useDebouncedValue(todayFilters.query, 250)
  const effectiveTodayFilters = useMemo(
    () => ({ ...todayFilters, query: debouncedTodayQuery }),
    [todayFilters, debouncedTodayQuery]
  )
  const filteredTodayJobs = useMemo(
    () => filterJobs(todayJobs, effectiveTodayFilters),
    [todayJobs, effectiveTodayFilters]
  )
  const visibleJobIds = useMemo(() => new Set(filteredTodayJobs.map(job => job.id)), [filteredTodayJobs])
  const actionableSelected = useMemo(() => selected.filter(id => visibleJobIds.has(id)), [selected, visibleJobIds])

  useEffect(() => {
    setSelected(previous => {
      const next = previous.filter(id => visibleJobIds.has(id))
      return next.length === previous.length ? previous : next
    })
  }, [visibleJobIds])

  useEffect(() => {
    const handleConfigSaved = () => { void refresh() }
    window.addEventListener('bosshunter-config-saved', handleConfigSaved)
    return () => window.removeEventListener('bosshunter-config-saved', handleConfigSaved)
  }, [refresh])

  const pendingGreetingJobs = workbench.pending_greetings
  const activeTask = workbench.task
  const visibleTask = activeTask || workbench.last_task
  const visibleTaskError = visibleTask?.error ? taskErrorFeedback(visibleTask.error) : null
  const pendingReplies = history.filter(item => item.action === 'reply_pending')

  const toggleJob = (id: string) => {
    setSelected(prev => (prev.includes(id) ? prev.filter(item => item !== id) : [...prev, id]))
  }

  const runPreflight = async (mode: WorkbenchMode, options?: Record<string, unknown>) => {
    setPreflightMode(mode)
    const res = options
      ? await fetch('/api/workbench/preflight', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, options }),
      })
      : await fetch(`/api/workbench/preflight?mode=${mode}`)
    const data = await parsePreflightResponse(res)
    setPreflightChecks(data.checks)
    if (!data.ok) {
      setNotice('请按提示处理后再启动')
      return false
    }
    return true
  }

  const handleModeClick = async (mode: WorkbenchMode) => {
    try {
      if (activeTask?.mode === mode) {
        if (window.confirm(`是否停止当前${activeTask.label}任务？已入库岗位会保留。`)) {
          setModePending(mode)
          setNotice(`正在停止${activeTask.label}...`)
          await stopTask(activeTask.id)
          setNotice(`${activeTask.label}已请求停止。`)
        }
        return
      }
      if (modePending) return
      if (activeTask) {
        setNotice(
          activeTask.status === 'stopping'
            ? `当前${activeTask.label}正在停止，请等待后台完全结束后再启动其他模式。`
            : `当前正在运行${activeTask.label}，请先点击橙色卡片停止后再启动其他模式。`
        )
        return
      }
      if (mode === 'full') {
        setCollectDialogMode('full')
        setCollectDialogOpen(true)
        return
      }
      const target = modes.find(item => item.mode === mode)
      setModePending(mode)
      setNotice(`${target?.title || '任务'}启动前预检中...`)
      if (!(await runPreflight(mode))) return
      setNotice(`${target?.title || '任务'}启动中，请稍候...`)
      await startTask(mode)
      setNotice(`${target?.title || '任务'}已启动，日志会在下方更新。`)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '操作失败')
    } finally {
      setModePending(null)
    }
  }

  const retryPreflight = async () => {
    if (modePending) return
    try {
      setModePending(preflightMode)
      setNotice('正在重新检查运行环境...')
      const ok = await runPreflight(preflightMode)
      setNotice(ok ? '' : '仍有问题需要处理，请查看检查结果。')
    } catch {
      setNotice('重新检查失败，请确认 BossHunter 后端仍在运行。')
    } finally {
      setModePending(null)
    }
  }

  const runStandalonePreflight = async () => {
    if (modePending || preflightRunning) return
    try {
      setPreflightRunning(true)
      setNotice('正在检查全流程运行环境...')
      const ok = await runPreflight('full')
      setNotice(ok ? '全流程预检通过，可以开始任务。' : '仍有问题需要处理，请查看检查结果。')
    } catch {
      setNotice('预检失败，请确认 BossHunter 后端仍在运行。')
    } finally {
      setPreflightRunning(false)
    }
  }

  const startCollection = async (options: Record<string, unknown>) => {
    const mode = collectDialogMode
    setModePending(mode)
    setNotice(mode === 'full' ? '全流程启动前预检中...' : '岗位采集启动前预检中...')
    try {
      if (!(await runPreflight(mode, options))) return
      await startTask(mode, options)
      setCollectDialogOpen(false)
      setNotice(mode === 'full' ? '全流程已启动，进度会在下方更新。' : '岗位采集已启动，进度会在下方更新。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '岗位采集启动失败')
    } finally {
      setModePending(null)
    }
  }

  const confirmDeliver = async (ids: string[]) => {
    if (!ids.length) return
    const count = ids.length
    if (!window.confirm(`是否投递以下 ${count} 个岗位？确认后将进入投递/打招呼流程。`)) return
    try {
      const res = await fetch('/api/workbench/deliver', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_ids: ids }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '投递失败')
      }
      const data = await res.json().catch(() => ({}))
      if (!ids.some(id => workbench.send_errors.some(job => job.id === id))) {
        setConfirmedDeliveryIds(prev => new Set([...prev, ...ids]))
      }
      await refresh()
      setNotice(
        data.already_queued_count === count
          ? `所选 ${count} 个岗位已在当前发送队列中。`
          : data.queued_count
            ? `已将 ${data.queued_count} 个岗位追加到当前发送队列。`
            : `已确认投递 ${count} 个岗位，后端会按队列推进。`
      )
      setSelected(prev => prev.filter(id => !new Set(ids).has(id)))
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '投递失败')
    }
  }

  const rejectSelectedJobs = async (ids: string[]) => {
    if (!ids.length) return
    const count = ids.length
    if (!window.confirm(`确定放弃这 ${count} 个岗位吗？放弃后不会进入投递，可在岗位池中查看已拒绝状态。`)) return
    try {
      const res = await fetch('/api/workbench/reject', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_ids: ids }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '放弃失败')
      }
      const rejectedIds = new Set(ids)
      setSelected(prev => prev.filter(id => !rejectedIds.has(id)))
      setConfirmedDeliveryIds(prev => new Set([...prev, ...ids]))
      await refresh()
      setNotice(`已放弃 ${count} 个岗位。`)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '放弃失败')
    }
  }

  const sendReadyGreetings = async (ids: string[]) => {
    if (!ids.length) return
    const count = ids.length
    try {
      const res = await fetch('/api/workbench/deliver', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_ids: ids, direct_send: true }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '发送失败')
      }
      const data = await res.json().catch(() => ({}))
      await refresh()
      setNotice(
        data.already_queued_count === count
          ? `所选 ${count} 个岗位已在当前发送队列中，请等待依次发送。`
          : data.queued_count
            ? `已将 ${data.queued_count} 个岗位追加到当前发送队列。`
            : `已直接进入发送流程 ${count} 个岗位。`
      )
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '发送失败')
    }
  }

  const openJobDetail = async (job: Job) => {
    try {
      const res = await fetch(`/api/jobs/${job.id}`)
      if (!res.ok) throw new Error('读取岗位详情失败')
      setSelectedJob(await res.json())
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '读取岗位详情失败')
    }
  }

  const downloadResume = (job: Job) => {
    window.open(`/api/jobs/${job.id}/resume/download`, '_blank')
  }

  const markResumeSent = async (job: Job) => {
    try {
      const res = await fetch(`/api/jobs/${job.id}/mark-resume-sent`, { method: 'POST' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '标记失败')
      }
      await refresh()
      setNotice(`已标记 ${job.company}｜${job.title} 的定制简历已发送。`)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '标记失败')
    }
  }

  if (loading) {
    return <div className="flex h-full items-center justify-center text-sm text-muted">加载中...</div>
  }

  if (view === 'jobs') {
    return <JobsPoolView />
  }

  if (view === 'monitor') {
    return (
      <MonitorExecutionView
        history={history}
        refresh={refresh}
        refreshing={refreshing}
        lastRefreshedAt={lastRefreshedAt}
      />
    )
  }

  return (
    <div className="space-y-5">
      <section id="today-workbench" className="scroll-mt-6 rounded-3xl border border-card-border bg-white p-5 shadow-sm">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <div className="text-xs font-black tracking-[0.18em] text-primary">TODAY WORKBENCH</div>
            <h2 className="mt-1 text-3xl font-black tracking-tight">今日求职行动</h2>
          </div>
          <div className="flex items-center gap-2">
            <div className="text-right">
              <Button variant="secondary" size="sm" onClick={refresh} disabled={refreshing}>
                <RefreshCw className={cn('mr-2 h-4 w-4', refreshing && 'animate-spin')} />
                {refreshing ? '刷新中' : '刷新'}
              </Button>
              {lastRefreshedAt && (
                <div className="mt-1 text-[10px] text-muted">
                  最后刷新：{lastRefreshedAt.toLocaleTimeString('zh-CN', { hour12: false })}
                </div>
              )}
            </div>
            <Button variant="secondary" size="sm" onClick={runStandalonePreflight} disabled={refreshing || Boolean(modePending) || preflightRunning}>
              <ShieldCheck className={cn('mr-2 h-4 w-4', preflightRunning && 'animate-spin')} />
              {preflightRunning ? '预检中' : '全流程预检'}
            </Button>
            <span className="rounded-full bg-[#FFF0E5] px-3 py-2 text-xs font-black text-primary">
              {activeTask ? `${activeTask.label}中` : '当前空闲'}
            </span>
          </div>
        </div>


        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {modes.map(item => {
            const isActive = activeTask?.mode === item.mode
            const disabled = Boolean(activeTask && !isActive)
            return (
              <button
                key={item.mode}
                onClick={() => {
                  if (isActive) {
                    void handleModeClick(item.mode)
                    return
                  }
                  if (disabled) {
                    setNotice(`当前正在运行${activeTask?.label || '其他任务'}，请先停止后再启动岗位采集。`)
                    return
                  }
                  if (item.mode === 'collect' || item.mode === 'full') {
                    setCollectDialogMode(item.mode)
                    setCollectDialogOpen(true)
                  }
                  else void handleModeClick(item.mode)
                }}
                aria-disabled={disabled}
                className={`min-h-[126px] rounded-3xl p-5 text-left transition ${
                  isActive
                    ? 'border-2 border-primary bg-primary text-white shadow-xl shadow-primary/20'
                    : disabled
                      ? 'cursor-not-allowed border border-card-border bg-white text-muted opacity-45'
                      : 'border border-card-border bg-[#FFFCFA] text-foreground hover:border-primary/60 hover:shadow-md'
                }`}
              >
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="text-lg font-black">
                    {modePending === item.mode
                      ? isActive ? '任务停止中' : '任务启动中'
                      : isActive ? `${item.title}中` : item.title}
                  </div>
                  {isActive ? <Square className="h-5 w-5 fill-current" /> : <Play className="h-5 w-5" />}
                </div>
                <p className={`text-xs leading-6 ${isActive ? 'text-white/85' : 'text-muted'}`}>{item.description}</p>
              </button>
            )
          })}
        </div>
        {notice && <div className="mt-3 rounded-2xl bg-[#FFF0E5] px-4 py-3 text-sm text-primary">{notice}</div>}
        {preflightChecks.some(check => check.status !== 'pass') && (
          <PreflightPanel checks={preflightChecks} checking={Boolean(modePending) || preflightRunning} onRetry={retryPreflight} />
        )}
        {error && <div className="mt-3 rounded-2xl bg-red-50 px-4 py-3 text-sm text-danger">{error}</div>}
        {visibleTask && (
          <div className="mt-3 rounded-3xl border border-card-border bg-[#FFFCFA] p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="text-sm font-black">任务运行状态</div>
                <p className="mt-1 text-xs leading-5 text-muted">如果点击后浏览器没有反应，请先打开 BOSS 直聘并确认已登录；常见失败原因是 BOSS 未登录或 Chrome 调试连接不可用。</p>
              </div>
              <span className="rounded-full bg-[#FFF0E5] px-3 py-1 text-xs font-black text-primary">
                {visibleTask.label}
              </span>
            </div>
            <div className={`mt-3 rounded-2xl border px-4 py-3 ${taskStatusClass(visibleTask.status)}`}>
              <div className="text-xs font-black text-primary">{taskStatusTitle(visibleTask.status)}</div>
              <div className="mt-1 whitespace-pre-line text-lg font-black leading-7 text-foreground">{currentTaskStage(visibleTask)}</div>
              <div className="mt-1 text-xs font-bold text-muted">任务状态：{taskStatusText(visibleTask.status)}</div>
              {visibleTask.deadline_at && (
                <div className="mt-1 text-xs font-bold text-muted">
                  自动截止：{new Date(visibleTask.deadline_at).toLocaleString('zh-CN', { hour12: false })}
                </div>
              )}
              {visibleTask.metrics && taskMetricItems.some(item => item.key in visibleTask.metrics!) && (
                <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
                  {taskMetricItems.map(item => (
                    <div key={item.key} className="rounded-xl border border-card-border bg-white px-3 py-2">
                      <div className="text-[10px] font-bold text-muted">{item.label}</div>
                      <div className="mt-0.5 text-lg font-black text-foreground">{visibleTask.metrics?.[item.key] ?? 0}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            {visibleTask.progress?.platforms && <CollectionProgressPanel progress={visibleTask.progress} />}
            {visibleTask.error && visibleTaskError && (
              <div className="mt-3 rounded-2xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-danger">
                <div className="font-black">{visibleTaskError.title}</div>
                <p className="mt-1 text-xs leading-5">{visibleTaskError.detail}</p>
                <details className="mt-2 text-xs text-muted">
                  <summary className="cursor-pointer font-bold">查看原始错误</summary>
                  <pre className="mt-2 whitespace-pre-wrap break-words rounded-lg bg-white p-2">{visibleTask.error}</pre>
                </details>
              </div>
            )}
            {visibleTask.stop_reason && (
              <div className={`mt-3 rounded-2xl px-3 py-3 text-sm ${visibleTask.stop_reason === 'daily_limit' ? 'border border-amber-200 bg-amber-50 text-amber-800' : 'bg-[#FFF0E5] text-primary'}`}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="font-black">{visibleTask.stop_reason === 'daily_limit' ? '本次未发送' : '任务说明'}</div>
                    <div className="mt-1">{taskStopReasonLabel(visibleTask.stop_reason)}</div>
                  </div>
                  {visibleTask.stop_reason === 'daily_limit' && (
                    <Button size="sm" variant="secondary" onClick={() => { window.location.href = '/config?section=throttle' }}>
                      去设置发送额度
                    </Button>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </section>

      {workbench.send_quota?.exhausted && (
        <section className="rounded-3xl border border-amber-200 bg-amber-50 p-5 text-amber-800">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-lg font-black">今日发送额度已用完</h3>
              <p className="mt-1 text-sm leading-6">
                今日已发送 {workbench.send_quota.sent}/{workbench.send_quota.daily_limit} 条，未发送岗位已保留在“待发送招呼语”；明日额度恢复后再重试。
              </p>
            </div>
            <Button variant="secondary" size="sm" onClick={() => { window.location.href = '/config?section=throttle' }}>
              去设置发送额度
            </Button>
          </div>
        </section>
      )}

      <section>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-lg font-black">求职数据</h3>
            <p className="mt-0.5 text-xs text-muted">今日看行动节奏，累计看岗位池沉淀。</p>
          </div>
          <div className="inline-flex rounded-full border border-card-border bg-white p-1">
            {([
              { value: 'today' as const, label: '今日数据' },
              { value: 'total' as const, label: '累计数据' },
            ]).map(option => (
              <button
                key={option.value}
                type="button"
                onClick={() => setStatsScope(option.value)}
                className={`rounded-full px-3 py-1.5 text-xs font-black transition ${
                  statsScope === option.value ? 'bg-primary text-white shadow-sm' : 'text-muted hover:text-primary'
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          {statItems.map(item => {
            const currentValue = workbench.pending_confirmation.length
            const selectedFunnel = statsScope === 'today' ? workbench.funnel_today : workbench.funnel
            const alternateFunnel = statsScope === 'today' ? workbench.funnel : workbench.funnel_today
            const value = item.current ? currentValue : (selectedFunnel[item.key] || 0)
            const supportingText = item.current
              ? '实时待处理数量'
              : `${statsScope === 'today' ? '累计' : '今日'} ${alternateFunnel[item.key] || 0}`
            return (
              <div key={item.key} className="rounded-2xl border border-card-border bg-white p-4">
                <div className="text-xs text-muted">{statsScope === 'today' ? item.todayLabel : item.totalLabel}</div>
                <div className={`mt-1 text-2xl font-black ${item.highlight ? 'text-primary' : 'text-foreground'}`}>
                  {value}
                </div>
                <div className="mt-1 text-[10px] font-bold text-muted">{supportingText}</div>
              </div>
            )
          })}
        </div>
      </section>

      <section className="rounded-3xl border border-card-border bg-white p-5">
        <div className="mb-4 flex items-center justify-between gap-4">
          <div>
            <h3 className="text-lg font-black">优先处理：HR 要简历 / 定制简历下载</h3>
            <p className="mt-1 text-xs text-muted">首页只展示需要你手动下载并自行发给 HR 的定制简历事项。</p>
          </div>
          <Button variant="secondary" size="sm">查看全部简历事项</Button>
        </div>
        {workbench.needs_resume.length ? (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {workbench.needs_resume.slice(0, 4).map(job => (
              <div key={job.id} className="rounded-2xl border border-card-border bg-[#FFFCFA] p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="font-black">{job.company}｜{job.title}</div>
                  <span className="rounded-full bg-[#FFF0E5] px-2 py-1 text-[11px] font-black text-primary">待发简历</span>
                </div>
                <p className="mt-2 text-sm leading-6 text-muted">HR 已请求简历，系统已准备定制化简历下载入口。</p>
                <div className="mt-3 flex gap-2">
                  <Button size="sm" onClick={() => downloadResume(job)}><Download className="mr-2 h-4 w-4" />下载定制简历</Button>
                  <Button variant="secondary" size="sm" onClick={() => markResumeSent(job)}>标记已发送</Button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-2xl border border-dashed border-card-border bg-[#FFFCFA] p-5 text-sm text-muted">当前没有 HR 要简历事项。</div>
        )}
      </section>

      {workbench.send_errors.length > 0 && (
        <section className="rounded-3xl border border-red-100 bg-red-50 p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
            <div>
              <h3 className="text-lg font-black text-danger">发送失败待处理</h3>
              <p className="mt-1 text-xs text-danger/80">这些岗位已生成招呼语，但没有成功发送。你可以重试，或放弃已失效岗位。</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" onClick={() => confirmDeliver(workbench.send_errors.map(job => job.id))}>重新发送全部 {workbench.send_errors.length} 个</Button>
              <Button variant="secondary" size="sm" onClick={() => rejectSelectedJobs(workbench.send_errors.map(job => job.id))}>放弃全部</Button>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {workbench.send_errors.map(job => (
              <div key={job.id} className="rounded-2xl border border-red-100 bg-white p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-black">{job.company}｜{job.title}</div>
                    <div className="mt-1 text-xs text-danger">最近失败原因：{job.last_error || '发送失败，等待重试'}</div>
                  </div>
                  <span className="rounded-full bg-red-50 px-2 py-1 text-[11px] font-black text-danger">发送失败</span>
                </div>
                <p className="mt-3 line-clamp-2 text-sm leading-6 text-muted">{job.greeting || '招呼语已生成，等待重新发送。'}</p>
                <div className="mt-3 flex gap-2">
                  <Button size="sm" onClick={() => sendReadyGreetings([job.id])}>重新发送</Button>
                  <Button variant="secondary" size="sm" onClick={() => rejectSelectedJobs([job.id])}>放弃</Button>
                  <Button variant="secondary" size="sm" onClick={() => openJobDetail(job)}><Eye className="mr-2 h-4 w-4" />查看详情</Button>
                  <Button variant="secondary" size="sm" disabled={!job.url} onClick={() => window.open(job.url, '_blank', 'noopener,noreferrer')}><ExternalLink className="mr-2 h-4 w-4" />跳转岗位链接</Button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {pendingGreetingJobs.length > 0 && (
        <section className="rounded-3xl border border-primary/20 bg-[#FFF0E5] p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
            <div>
              <h3 className="text-lg font-black">待发送招呼语</h3>
              <p className="mt-1 text-xs text-muted">这些岗位已确认并生成招呼语，点击后会直接进入发送流程。</p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" onClick={() => sendReadyGreetings(pendingGreetingJobs.map(job => job.id))}>发送全部 {pendingGreetingJobs.length} 个</Button>
              <Button variant="secondary" size="sm" onClick={() => rejectSelectedJobs(pendingGreetingJobs.map(job => job.id))}>放弃全部</Button>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {pendingGreetingJobs.map(job => (
              <div key={job.id} className="rounded-2xl border border-primary/20 bg-white p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-black">{job.company}｜{job.title}</div>
                    <div className="mt-1 text-xs text-primary">已生成招呼语，等待发送</div>
                  </div>
                  <span className="rounded-full bg-[#FFF0E5] px-2 py-1 text-[11px] font-black text-primary">待发送</span>
                </div>
                <p className="mt-3 line-clamp-2 text-sm leading-6 text-muted">{job.greeting || '招呼语已生成，等待发送。'}</p>
                <div className="mt-3 flex gap-2">
                  <Button size="sm" onClick={() => sendReadyGreetings([job.id])}>发送招呼语</Button>
                  <Button variant="secondary" size="sm" onClick={() => rejectSelectedJobs([job.id])}>放弃</Button>
                  <Button variant="secondary" size="sm" onClick={() => openJobDetail(job)}><Eye className="mr-2 h-4 w-4" />查看详情</Button>
                  <Button variant="secondary" size="sm" disabled={!job.url} onClick={() => window.open(job.url, '_blank', 'noopener,noreferrer')}><ExternalLink className="mr-2 h-4 w-4" />跳转岗位链接</Button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="rounded-3xl border border-card-border bg-white p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
          <div>
            <h3 className="text-lg font-black">今日待确认</h3>
            <p className="mt-1 text-xs text-muted">展示需要你人工确认是否推进投递的岗位，支持全选、部分选择、一键投递。</p>
          </div>
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={() => setSelected(filteredTodayJobs.map(job => job.id))}>全选</Button>
            <Button variant="secondary" size="sm" onClick={() => setSelected([])}>清空</Button>
            <Button variant="secondary" size="sm" onClick={() => rejectSelectedJobs(actionableSelected)}>放弃已选 {actionableSelected.length} 个</Button>
            <Button size="sm" onClick={() => confirmDeliver(actionableSelected)}>一键投递已选 {actionableSelected.length} 个</Button>
          </div>
        </div>
        <JobFilterBar
          filters={todayFilters}
          onChange={setTodayFilters}
          onReset={() => setTodayFilters({ ...EMPTY_JOB_FILTERS })}
          resultCount={filteredTodayJobs.length}
          totalCount={todayJobs.length}
          invalidSalary={hasInvalidSalaryRange(todayFilters)}
        />
        {filteredTodayJobs.length ? (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {filteredTodayJobs.map(job => (
              <JobActionCard
                key={job.id}
                job={job}
                selected={selected.includes(job.id)}
                onToggle={() => toggleJob(job.id)}
                onDetail={() => openJobDetail(job)}
                onReject={() => rejectSelectedJobs([job.id])}
              />
            ))}
          </div>
        ) : todayJobs.length ? (
          <div className="rounded-2xl border border-dashed border-card-border bg-[#FFFCFA] p-5 text-center text-sm text-muted">
            <p>没有符合当前条件的岗位</p>
            <Button className="mt-3" variant="secondary" size="sm" onClick={() => setTodayFilters({ ...EMPTY_JOB_FILTERS })}>重置筛选</Button>
          </div>
        ) : (
          <div className="rounded-2xl border border-dashed border-card-border bg-[#FFFCFA] p-5 text-sm text-muted">今天暂时没有待确认岗位。</div>
        )}
      </section>

      {selectedJob && <JobDetailModal job={selectedJob} onClose={() => setSelectedJob(null)} />}
      <CollectJobsDialog
        open={collectDialogOpen}
        mode={collectDialogMode}
        activeTask={activeTask && (activeTask.mode === 'collect' || activeTask.mode === 'full') ? activeTask : null}
        onClose={() => setCollectDialogOpen(false)}
        onStart={options => void startCollection(options)}
      />
    </div>
  )
}

function CollectionProgressPanel({ progress }: { progress: CollectionProgress }) {
  return (
    <div className="mt-3 rounded-2xl border border-primary/20 bg-[#FFF0E5] p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-black text-primary">多平台采集进度</div>
        <div className="text-xs font-bold text-muted">{progress.outcome === 'running' ? '执行中' : progress.outcome || '已结束'}</div>
      </div>
      <div className="mt-3 grid gap-2 md:grid-cols-2">
        {Object.entries(progress.platforms || {}).map(([platform, state]) => (
          <div key={platform} className="rounded-xl border border-card-border bg-white p-3">
            <div className="flex items-center justify-between text-sm font-black">
              <span>{platform === 'boss' ? 'BOSS 直聘' : platform === 'zhilian' ? '智联招聘' : '前程无忧'}</span>
              <span>新增 {state.new}</span>
            </div>
            <div className="mt-1 text-xs text-muted">
              {state.status === 'queued' ? '等待前序平台完成' : `${state.city || '城市未开始'} · ${state.keyword || '关键词未开始'} · 第 ${state.page || 0}/${state.max_pages || 0} 页`}
            </div>
            <div className="mt-1 text-xs text-muted">扫描 {state.seen || 0} · 重复 {state.duplicate || 0} · 过滤 {state.filtered || 0} · 解析失败 {state.parse_failed || 0} · 保存失败 {state.save_failed || 0}</div>
            {(state.message || state.reason_code) && <div className="mt-1 text-xs font-bold text-primary">{state.message || state.reason_code}</div>}
          </div>
        ))}
      </div>
    </div>
  )
}

function JobActionCard({ job, selected, onToggle, onDetail, onReject }: { job: Job; selected: boolean; onToggle: () => void; onDetail: () => void; onReject: () => void }) {
  return (
    <div className={`rounded-2xl border p-4 ${selected ? 'border-primary bg-[#FFFCFA]' : 'border-card-border bg-[#FFFCFA]'}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-black">{job.company}｜{job.title}</div>
          <div className="mt-1 text-xs text-muted">{jobSubtitle(job)}</div>
        </div>
        <input type="checkbox" checked={selected} onChange={onToggle} className="mt-1 h-4 w-4 accent-primary" />
      </div>
      <p className="mt-3 line-clamp-2 text-sm leading-6 text-muted">{job.score_reason || job.greeting || '等待继续推进。'}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="secondary" size="sm" onClick={onDetail}><Eye className="mr-2 h-4 w-4" />查看详情</Button>
        <Button variant="secondary" size="sm" disabled={!job.url} onClick={() => window.open(job.url, '_blank', 'noopener,noreferrer')}><ExternalLink className="mr-2 h-4 w-4" />跳转岗位链接</Button>
        <Button variant="secondary" size="sm" onClick={onReject}><XCircle className="mr-2 h-4 w-4" />放弃岗位</Button>
      </div>
    </div>
  )
}

function JobDetailModal({ job, onClose }: { job: Job; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-6">
      <div className="max-h-[86vh] w-full max-w-3xl overflow-y-auto rounded-3xl border border-card-border bg-white p-6 shadow-2xl">
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <div className="text-xs font-black tracking-[0.18em] text-primary">岗位详情</div>
            <h3 className="mt-1 text-2xl font-black">{job.company}｜{job.title}</h3>
            <p className="mt-1 text-sm text-muted">{job.salary || '薪资未填'} · {job.city || '城市未填'} · {getStatusLabel(job.status)}</p>
          </div>
          <Button variant="secondary" size="sm" onClick={onClose}>关闭</Button>
        </div>
        <div className="grid gap-3 text-sm lg:grid-cols-2">
          <InfoBlock label="HR" value={[job.hr_name, job.hr_title].filter(Boolean).join(' · ') || '-'} />
          <InfoBlock label="招聘者活跃" value={job.hr_active || '活跃度未知'} />
          <InfoBlock label="公司" value={[job.company_size, job.company_industry].filter(Boolean).join(' · ') || '-'} />
          <InfoBlock label="来源平台" value={job.source_platform === 'zhilian' ? '智联招聘｜当前只开放采集' : job.source_platform === '51job' ? '前程无忧｜当前只开放采集' : 'BOSS 直聘'} />
          <InfoBlock label="匹配分" value={String(job.score || '-')} />
          <InfoBlock label="定制简历" value={job.resume_path || '未生成'} />
        </div>
        <div className="mt-4 rounded-2xl border border-card-border bg-[#FFFCFA] p-4">
          <div className="text-sm font-black">评分理由</div>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted">{job.score_reason || '-'}</p>
        </div>
        <div className="mt-4 rounded-2xl border border-card-border bg-[#FFFCFA] p-4">
          <div className="text-sm font-black">招呼语</div>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted">{job.greeting || '未生成'}</p>
        </div>
        <div className="mt-4 rounded-2xl border border-card-border bg-[#FFFCFA] p-4">
          <div className="text-sm font-black">JD</div>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted">{job.jd || '-'}</p>
        </div>
      </div>
    </div>
  )
}

function InfoBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-card-border bg-[#FFFCFA] p-4">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 font-bold text-foreground">{value}</div>
    </div>
  )
}

function JobsPoolView() {
  const pageSize = 15
  const [page, setPage] = useState(0)
  const [filters, setFilters] = useState<JobFilters>({ ...EMPTY_JOB_FILTERS })
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  const [showRecycleBin, setShowRecycleBin] = useState(false)
  const [showScoreDialog, setShowScoreDialog] = useState(false)
  const [quickScoring, setQuickScoring] = useState(false)
  const [sortBy, setSortBy] = useState<JobSortKey>('created_at')
  const [sortOrder, setSortOrder] = useState<JobSortOrder>('desc')
  const [recycleJobs, setRecycleJobs] = useState<Job[]>([])
  const [recycleSelectedIds, setRecycleSelectedIds] = useState<string[]>([])
  const [recycleLoading, setRecycleLoading] = useState(false)
  const [permanentDeleteIds, setPermanentDeleteIds] = useState<string[]>([])
  const [permanentDeleteAcknowledged, setPermanentDeleteAcknowledged] = useState(false)
  const { items, total, allTotal, loading, error, refresh: refreshJobs } = useJobSearch(filters, page, pageSize, sortBy, sortOrder)
  const { workbench: deliveryWorkbench } = useDashboard('workbench')
  const deliveryTask = deliveryWorkbench.task?.mode === 'deliver'
    ? deliveryWorkbench.task
    : deliveryWorkbench.last_task?.mode === 'deliver' ? deliveryWorkbench.last_task : null

  useEffect(() => {
    setPage(0)
  }, [filters.query, filters.minScore, filters.salaryMin, filters.salaryMax, filters.status, filters.createdWithin, filters.sourcePlatform, filters.education, filters.recruitmentType])

  const toggleSelected = (jobId: string) => {
    setSelectedIds(previous => previous.includes(jobId) ? previous.filter(id => id !== jobId) : [...previous, jobId])
  }

  const allPageSelected = items.length > 0 && items.every(job => selectedIds.includes(job.id))
  const toggleCurrentPage = () => {
    const pageIds = new Set(items.map(job => job.id))
    setSelectedIds(previous => allPageSelected
      ? previous.filter(id => !pageIds.has(id))
      : [...new Set([...previous, ...pageIds])])
  }

  const changeSort = (nextSortBy: JobSortKey) => {
    setPage(0)
    if (nextSortBy === sortBy) {
      setSortOrder(previous => previous === 'asc' ? 'desc' : 'asc')
      return
    }
    setSortBy(nextSortBy)
    setSortOrder(nextSortBy === 'score' || nextSortBy === 'created_at' ? 'desc' : 'asc')
  }

  const loadRecycleBin = async () => {
    setRecycleLoading(true)
    try {
      const collected: Job[] = []
      let offset = 0
      const limit = 200
      while (true) {
        const res = await fetch(`/api/jobs?deleted=only&limit=${limit}&offset=${offset}`, { cache: 'no-store' })
        if (!res.ok) throw new Error(`回收站接口返回 ${res.status}`)
        const pageItems = await res.json()
        if (!Array.isArray(pageItems)) throw new Error('回收站响应格式无效')
        collected.push(...pageItems)
        const totalCount = Number(res.headers.get('X-Total-Count'))
        if (!pageItems.length || pageItems.length < limit || (Number.isFinite(totalCount) && collected.length >= totalCount)) break
        offset += pageItems.length
      }
      const unique = new Map(collected.map(job => [String(job.id), job]))
      setRecycleJobs([...unique.values()])
      setRecycleSelectedIds(previous => previous.filter(id => unique.has(id)))
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '读取回收站失败')
    } finally {
      setRecycleLoading(false)
    }
  }

  useEffect(() => {
    void loadRecycleBin()
  }, [])

  const postJobAction = async (path: string, payload: Record<string, unknown>) => {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) {
      const data = await res.json().catch(() => ({}))
      const blocked = Array.isArray(data.blocked)
        ? data.blocked.map((item: { job_id?: string; reasons?: string[] }) => `${item.job_id || '岗位'}：${(item.reasons || []).join('、')}`).join('；')
        : ''
      throw new Error([data.error || '岗位操作失败', blocked].filter(Boolean).join('；'))
    }
    return res.json()
  }

  const softDelete = async (jobIds: string[]) => {
    if (!jobIds.length || !window.confirm(`确认将 ${jobIds.length} 个岗位移入回收站吗？岗位不会永久删除。`)) return
    try {
      const result = await postJobAction('/api/jobs/soft-delete', { job_ids: jobIds, confirmed: true })
      setSelectedIds(previous => previous.filter(id => !jobIds.includes(id)))
      refreshJobs()
      await loadRecycleBin()
      setNotice(`已移入回收站 ${result.affected_count || 0} 条岗位。`)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '移入回收站失败')
    }
  }

  const markManuallySent = async (job: Job) => {
    if (job.source_platform !== 'zhilian' && job.source_platform !== '51job') return
    const platformLabel = job.source_platform === 'zhilian' ? '智联招聘' : '前程无忧'
    if (!window.confirm(`请确认：你已经在${platformLabel}完成了这个岗位的投递。此操作只更新 BossHunter 本地记录，不会向平台发送任何内容。`)) return
    try {
      const result = await postJobAction('/api/jobs/manual-sent', {
        job_ids: [job.id],
        confirmed: true,
      })
      refreshJobs()
      setNotice(
        result.affected_count
          ? `已将 ${platformLabel} 岗位标记为“已发送”。`
          : `该岗位此前已经标记为“已发送”。`
      )
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '标记已发送失败')
    }
  }

  const deliverSelectedJobs = async () => {
    if (!selectedIds.length) return
    const count = selectedIds.length
    if (!window.confirm(`确认投递已选择的 ${count} 个岗位吗？仅 BOSS 岗位可进入发送队列，且仍受发送时间窗口和每日额度限制。`)) return
    try {
      const result = await postJobAction('/api/workbench/deliver', { job_ids: selectedIds })
      setSelectedIds([])
      refreshJobs()
      setNotice(
        result.already_queued_count === count
          ? `所选 ${count} 个岗位已在当前发送队列中。`
          : result.queued_count
            ? `已将 ${result.queued_count} 个岗位追加到当前发送队列。`
            : `已确认投递 ${count} 个岗位，后端会按安全队列推进。`
      )
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '一键投递失败')
    }
  }

  const restoreJobs = async (jobIds: string[]) => {
    if (!jobIds.length || !window.confirm(`确认恢复 ${jobIds.length} 个岗位吗？恢复后不会自动评分或投递。`)) return
    try {
      const result = await postJobAction('/api/jobs/restore', { job_ids: jobIds, confirmed: true })
      setRecycleSelectedIds(previous => previous.filter(id => !jobIds.includes(id)))
      refreshJobs()
      await loadRecycleBin()
      setNotice(`已恢复 ${result.affected_count || 0} 条岗位。`)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '恢复失败')
    }
  }

  const requestPermanentDelete = (jobIds: string[]) => {
    if (!jobIds.length) return
    setPermanentDeleteIds(jobIds)
    setPermanentDeleteAcknowledged(false)
  }

  const confirmPermanentDelete = async () => {
    if (!permanentDeleteIds.length || !permanentDeleteAcknowledged) return
    try {
      const result = await postJobAction('/api/jobs/permanent-delete', {
        job_ids: permanentDeleteIds,
        confirmed: true,
        confirmation: 'PERMANENT_DELETE',
      })
      setRecycleSelectedIds(previous => previous.filter(id => !permanentDeleteIds.includes(id)))
      setPermanentDeleteIds([])
      setPermanentDeleteAcknowledged(false)
      await loadRecycleBin()
      setNotice(`已永久删除 ${result.affected_count || 0} 条岗位。`)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '永久删除失败')
    }
  }

  const exportJobs = async (format: 'xlsx' | 'csv', scope: 'all' | 'filtered' | 'selected') => {
    try {
      const res = await fetch('/api/jobs/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          format,
          scope,
          job_ids: scope === 'selected' ? selectedIds : [],
          filters: scope === 'filtered' ? {
            q: filters.query.trim(),
            min_score: filters.minScore,
            salary_min: filters.salaryMin,
            salary_max: filters.salaryMax,
            status: filters.status,
            created_within: filters.createdWithin,
            source_platform: filters.sourcePlatform,
          } : {},
        }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '导出失败')
      }
      const blob = await res.blob()
      const disposition = res.headers.get('Content-Disposition') || ''
      const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || `bosshunter-jobs.${format}`
      const url = window.URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = filename
      anchor.click()
      window.URL.revokeObjectURL(url)
      const exportedCount = Number(res.headers.get('X-Exported-Count'))
      setNotice(`已导出 ${Number.isFinite(exportedCount) ? exportedCount : 0} 条岗位。`)
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '导出失败')
    }
  }

  const startScoring = async (options: {
    scope: 'pending' | 'failed' | 'selected' | 'all_scored'
    limit: number | null
    job_ids: string[]
    force_rescore: boolean
    force?: boolean
  }) => {
    const res = await fetch('/api/scoring/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ options, force: options.force ?? false }),
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) {
      if (res.status === 409 && data.code === 'scoring_run_paused' && !options.force) {
        const confirmed = window.confirm(
          `已有等待恢复的评分任务：${data.error || ''}\n是否结束该任务并强制开始新评分任务？（已完成的评分结果会保留）`,
        )
        if (confirmed) {
          await startScoring({ ...options, force: true })
          return
        }
      }
      const checks = Array.isArray(data.messages) ? data.messages.join('；') : ''
      throw new Error([data.error || '启动评分失败', checks].filter(Boolean).join('：'))
    }
    setNotice(`独立评分已启动，共 ${data.run?.remaining_job_ids?.length || 0} 个岗位。`)
  }

  const startQuickScoring = async () => {
    if (!window.confirm('将对岗位池中所有未评分或评分失败的岗位启动 AI 评分，可能产生模型费用，是否继续？')) return
    setQuickScoring(true)
    try {
      await startScoring({ scope: 'pending', limit: null, job_ids: [], force_rescore: false })
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '启动 AI 评分失败')
    } finally {
      setQuickScoring(false)
    }
  }

  if (showRecycleBin) {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <Button variant="ghost" size="sm" onClick={() => setShowRecycleBin(false)}>返回岗位池</Button>
          <Button variant="secondary" size="sm" onClick={() => void loadRecycleBin()} disabled={recycleLoading}>刷新回收站</Button>
        </div>
        {notice && <div className="rounded-xl bg-[#FFF0E5] px-4 py-3 text-sm text-primary">{notice}</div>}
        <RecycleBinPanel
          jobs={recycleJobs}
          selectedIds={recycleSelectedIds}
          loading={recycleLoading}
          onToggleSelected={id => setRecycleSelectedIds(previous => previous.includes(id) ? previous.filter(item => item !== id) : [...previous, id])}
          onSelectAll={setRecycleSelectedIds}
          onRestore={ids => void restoreJobs(ids)}
          onPermanentDelete={requestPermanentDelete}
        />
        {permanentDeleteIds.length > 0 && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4" role="dialog" aria-modal="true">
            <div className="w-full max-w-lg rounded-3xl border border-red-200 bg-white p-6 shadow-2xl">
              <div className="flex items-start gap-3"><AlertTriangle className="mt-0.5 h-6 w-6 shrink-0 text-danger" /><div><h3 className="text-xl font-black">确认永久删除</h3><p className="mt-2 text-sm leading-6 text-muted">将永久删除 {permanentDeleteIds.length} 条岗位及其历史，无法恢复。存在发送或回复证据的岗位会被后端拒绝删除。</p></div></div>
              <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-2xl border border-red-100 bg-red-50 p-3 text-sm font-bold"><input type="checkbox" checked={permanentDeleteAcknowledged} onChange={event => setPermanentDeleteAcknowledged(event.target.checked)} className="mt-0.5 h-4 w-4 accent-danger" /><span>我确认永久删除，并了解此操作无法撤销。</span></label>
              <div className="mt-6 flex justify-end gap-3"><Button variant="secondary" size="sm" onClick={() => setPermanentDeleteIds([])}>取消</Button><Button variant="destructive" size="sm" disabled={!permanentDeleteAcknowledged} onClick={() => void confirmPermanentDelete()}>永久删除</Button></div>
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="rounded-3xl border border-card-border bg-white p-5">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-black">岗位池</h2>
          <p className="mt-1 text-sm text-muted">集中查看已采集岗位、AI 分数、状态和详情入口。</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => { setShowRecycleBin(true); void loadRecycleBin() }}><Trash2 className="mr-1 h-4 w-4" />回收站 ({recycleJobs.length})</Button>
          <BriefcaseBusiness className="h-6 w-6 text-primary" />
        </div>
      </div>
      <JobFilterBar
        filters={filters}
        onChange={setFilters}
        onReset={() => setFilters({ ...EMPTY_JOB_FILTERS })}
        resultCount={total}
        totalCount={allTotal}
        invalidSalary={hasInvalidSalaryRange(filters)}
        showStatus
        showSource
      />
      <div className="mb-4 flex flex-wrap items-center gap-2 text-xs">
        <Button variant="secondary" size="sm" disabled={!items.length} onClick={toggleCurrentPage}>
          {allPageSelected ? '取消选择本页' : '选择本页'}
        </Button>
        <span className="rounded-full bg-[#FFF0E5] px-3 py-2 font-bold text-primary">已选择 {selectedIds.length} 条</span>
        {selectedIds.length > 0 && <Button variant="ghost" size="sm" onClick={() => setSelectedIds([])}>清空选择</Button>}
        <Button variant="destructive" size="sm" disabled={!selectedIds.length} onClick={() => void softDelete(selectedIds)}>移入回收站</Button>
        <Button size="sm" disabled={!selectedIds.length} onClick={() => void deliverSelectedJobs()}>
          <Send className="mr-1 h-4 w-4" />BOSS 一键投递已选
        </Button>
        <Button size="sm" onClick={() => void startQuickScoring()} disabled={quickScoring || !total}>
          {quickScoring ? '启动评分中…' : '一键 AI 评分'}
        </Button>
        <Button variant="secondary" size="sm" onClick={() => setShowScoreDialog(true)}>评分选项</Button>
        <ExportMenu onExport={exportJobs} hasSelection={selectedIds.length > 0} hasFiltered={total > 0} />
      </div>
      {notice && <div className="mb-4 rounded-xl bg-[#FFF0E5] px-4 py-3 text-sm text-primary">{notice}</div>}
      {deliveryTask && (
        <div className="mb-4 rounded-2xl border border-card-border bg-[#FFFCFA] p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="text-sm font-black">投递队列</div>
              <p className="mt-1 text-xs text-muted">只展示已人工确认的 BOSS 发送任务；智联和 51job 不会进入此队列。</p>
            </div>
            <span className="rounded-full bg-[#FFF0E5] px-3 py-1 text-xs font-black text-primary">
              {deliveryTask.status === 'running' ? '处理中' : deliveryTask.status === 'completed' ? '已完成' : deliveryTask.status === 'failed' ? '失败' : deliveryTask.status}
            </span>
          </div>
          <div className="mt-3 rounded-xl border border-card-border bg-white px-3 py-2 text-sm">
            <div className="font-bold">{deliveryTask.logs?.[deliveryTask.logs.length - 1] || '队列已创建，等待执行'}</div>
            <div className="mt-1 text-xs text-muted">任务 ID：{deliveryTask.id}</div>
          </div>
        </div>
      )}
      {error && <div className="mb-4 rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-sm text-danger">{error}</div>}
      <JobsTable
        jobs={items}
        page={page}
        pageSize={pageSize}
        total={total}
        onPageChange={setPage}
        selectedIds={selectedIds}
        onToggleSelected={toggleSelected}
        onSoftDelete={job => void softDelete([job.id])}
        onMarkManuallySent={job => void markManuallySent(job)}
        loading={loading}
        sortBy={sortBy}
        sortOrder={sortOrder}
        onSortChange={changeSort}
      />
      <ScoreJobsDialog
        open={showScoreDialog}
        selectedJobIds={selectedIds}
        onClose={() => setShowScoreDialog(false)}
        onStart={startScoring}
      />
    </div>
  )
}

function ExportMenu({
  onExport,
  hasSelection,
  hasFiltered,
}: {
  onExport: (format: 'xlsx' | 'csv', scope: 'all' | 'filtered' | 'selected') => void
  hasSelection: boolean
  hasFiltered: boolean
}) {
  const [format, setFormat] = useState<'xlsx' | 'csv'>('xlsx')
  return (
    <div className="ml-auto flex flex-wrap items-center gap-2">
      <select
        value={format}
        onChange={event => setFormat(event.target.value as 'xlsx' | 'csv')}
        className="rounded-xl border border-card-border bg-white px-2 py-2 text-xs outline-none focus:border-primary"
      >
        <option value="xlsx">XLSX</option>
        <option value="csv">CSV</option>
      </select>
      <Button variant="secondary" size="sm" disabled={!hasFiltered} onClick={() => onExport(format, 'filtered')}>导出筛选结果</Button>
      <Button variant="secondary" size="sm" disabled={!hasSelection} onClick={() => onExport(format, 'selected')}>导出所选岗位</Button>
      <Button variant="secondary" size="sm" onClick={() => onExport(format, 'all')}>导出全部岗位</Button>
    </div>
  )
}

type MonitorFilter = 'pending' | 'resume' | 'follow_up' | 'replied'
const REPLY_RESOLUTION_ACTIONS = ['reply_dismissed', 'replied', 'auto_replied']
const DETECTION_RESOLUTION_ACTIONS = [
  ...REPLY_RESOLUTION_ACTIONS,
  'reply_pending',
  'needs_resume',
  'resume_failed',
  'resume_sent',
  'rejected',
]

function uniqueLatestByJob(items: HistoryItem[]) {
  const seen = new Set<string>()
  return items.filter(item => {
    const key = item.job_id || `${item.company}-${item.title}-${item.action}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function sameHistoryJob(left: HistoryItem, right: HistoryItem) {
  if (left.job_id && right.job_id) return left.job_id === right.job_id
  return left.company === right.company && left.title === right.title
}

function isReplyPendingResolved(item: HistoryItem, history: HistoryItem[]) {
  return history.some(candidate =>
    candidate.id !== item.id
    && sameHistoryJob(item, candidate)
    && REPLY_RESOLUTION_ACTIONS.includes(candidate.action)
    && candidate.created_at >= item.created_at
  )
}

function isResumeFailureResolved(item: HistoryItem, history: HistoryItem[]) {
  return Boolean(item.resolved || item.resume_path) || history.some(candidate =>
    candidate.id > item.id
    && sameHistoryJob(item, candidate)
    && (candidate.action === 'needs_resume' || candidate.action === 'resume_sent')
  )
}

function isDetectedReplyResolved(item: HistoryItem, history: HistoryItem[]) {
  return history.some(candidate =>
    candidate.id > item.id
    && sameHistoryJob(item, candidate)
    && DETECTION_RESOLUTION_ACTIONS.includes(candidate.action)
  )
}

function isResumeRequestResolved(item: HistoryItem, history: HistoryItem[]) {
  return history.some(candidate =>
    candidate.id > item.id
    && sameHistoryJob(item, candidate)
    && candidate.action === 'resume_sent'
  )
}

function isOutboundReplyRecord(item: HistoryItem) {
  if (item.action === 'resume_sent') return true
  if (item.action === 'auto_replied') return true
  return item.action === 'replied' && parseHistoryDetail(item).schema.startsWith('replied.')
}

type MonitorConversationMessage = {
  sender: 'hr' | 'ai'
  text: string
}

function monitorConversationMessages(item: HistoryItem, history: HistoryItem[]): MonitorConversationMessage[] {
  const parsed = parseHistoryDetail(item)
  const pendingItem = parsed.pendingHistoryId
    ? history.find(candidate => candidate.id === parsed.pendingHistoryId)
    : undefined
  const pendingParsed = pendingItem ? parseHistoryDetail(pendingItem) : null
  const resumeRequestItem = item.action === 'resume_sent'
    ? history
      .filter(candidate =>
        candidate.id < item.id
        && candidate.action === 'needs_resume'
        && sameHistoryJob(item, candidate)
      )
      .sort((left, right) => right.id - left.id)[0]
    : undefined
  const resumeRequestParsed = resumeRequestItem ? parseHistoryDetail(resumeRequestItem) : null
  const source = parsed.conversationTail.length
    ? parsed
    : (pendingParsed || resumeRequestParsed || parsed)
  const messages: MonitorConversationMessage[] = []

  const append = (sender: 'hr' | 'ai', text: string) => {
    const normalizedText = text.trim()
    if (!normalizedText) return
    const previous = messages[messages.length - 1]
    if (previous?.sender === sender && previous.text === normalizedText) return
    messages.push({ sender, text: normalizedText })
  }

  source.conversationTail.forEach(message => {
    if (message.sender === 'hr') append('hr', message.text)
    if (message.sender === 'me') append('ai', message.text)
  })

  const hrQuestion = parsed.hrQuestion || source.hrQuestion
  if (!messages.some(message => message.sender === 'hr')) append('hr', hrQuestion)
  if (item.action !== 'resume_sent' && isOutboundReplyRecord(item) && !parsed.schema.startsWith('replied.external.')) {
    append('ai', parsed.aiReply)
  }

  return messages
}

function MonitorExecutionView({
  history,
  refresh,
  refreshing,
  lastRefreshedAt,
}: {
  history: HistoryItem[]
  refresh: () => Promise<void>
  refreshing: boolean
  lastRefreshedAt: Date | null
}) {
  const pendingReplies = uniqueLatestByJob(history.filter(item =>
    item.action === 'reply_pending' && !isReplyPendingResolved(item, history)
  ))
  const detectedReplies = uniqueLatestByJob(history.filter(item =>
    item.action === 'hr_reply_detected' && !isDetectedReplyResolved(item, history)
  ))
  const resumeFailures = uniqueLatestByJob(history.filter(item =>
    item.action === 'resume_failed' && !isResumeFailureResolved(item, history)
  ))
  const pendingItems = uniqueLatestByJob(
    [...detectedReplies, ...pendingReplies, ...resumeFailures].sort((left, right) => right.id - left.id)
  )
  const pendingResumeRequests = history.filter(item =>
    item.action === 'needs_resume' && !isResumeRequestResolved(item, history)
  )
  const resumeRequests = uniqueLatestByJob(
    [...pendingResumeRequests, ...resumeFailures].sort((left, right) => right.id - left.id)
  )
  const followUpRecords = uniqueLatestByJob(history.filter(item => item.action === 'follow_up_sent'))
  const repliedRecords = history.filter(isOutboundReplyRecord)
  const [activeMonitorFilter, setActiveMonitorFilter] = useState<MonitorFilter>('pending')
  const visibleHistory = activeMonitorFilter === 'resume'
    ? resumeRequests
    : activeMonitorFilter === 'follow_up'
      ? followUpRecords
      : activeMonitorFilter === 'replied'
        ? repliedRecords
        : pendingItems
  const displayedHistory = activeMonitorFilter === 'pending' || activeMonitorFilter === 'resume'
    ? visibleHistory
    : visibleHistory.slice(0, 8)
  const [replyDrafts, setReplyDrafts] = useState<Record<number, string>>({})
  const [notice, setNotice] = useState('')
  const [openingChatId, setOpeningChatId] = useState<number | null>(null)
  const [preparingReplyId, setPreparingReplyId] = useState<number | null>(null)

  const draftFor = (item: HistoryItem) => {
    const parsed = parseHistoryDetail(item)
    return replyDrafts[item.id] ?? parsed.aiReply ?? item.detail ?? ''
  }

  const sendManualReply = async (item: HistoryItem) => {
    try {
      const res = await fetch(`/api/history/${item.id}/reply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: draftFor(item) }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '回复失败')
      }
      await refresh()
      setNotice('回复已记录，请在招聘平台手动发送。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '回复失败')
    }
  }

  const dismissPendingReply = async (item: HistoryItem) => {
    if (!window.confirm('确定放弃这条待回复建议吗？放弃后不会发送消息，也不会把岗位标记为拒绝。')) return
    try {
      const res = await fetch(`/api/history/${item.id}/dismiss`, { method: 'POST' })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.error || '放弃失败')
      }
      await refresh()
      setNotice('已放弃这条待回复建议。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '放弃失败')
    }
  }

  const openMonitorConversation = async (item: HistoryItem) => {
    const platform = item.source_platform || 'boss'
    if (platform !== 'boss') {
      const targetUrl = monitorChatUrl(item)
      if (targetUrl) window.open(targetUrl, '_blank', 'noopener,noreferrer')
      return
    }
    setOpeningChatId(item.id)
    try {
      const res = await fetch(`/api/history/${item.id}/open-chat`, { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.error || '聊天定位失败')
      setNotice('已在 BOSS 中定位到对应聊天对话。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '聊天定位失败')
    } finally {
      setOpeningChatId(null)
    }
  }

  const prepareDetectedReply = async (item: HistoryItem) => {
    setPreparingReplyId(item.id)
    try {
      const res = await fetch(`/api/history/${item.id}/prepare-reply`, { method: 'POST' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.error || '对话读取失败')
      await refresh()
      setNotice(data.already_processed ? '这条消息已经处理。' : '完整对话已读取，请检查生成的处理结果。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '对话读取失败')
    } finally {
      setPreparingReplyId(null)
    }
  }

  return (
    <div className="rounded-3xl border border-card-border bg-white p-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-black">监测执行</h2>
          <p className="mt-1 text-sm text-muted">这里不启动监测，只处理监测发现的 HR 问题、回复建议和结果。</p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <span className="text-xs text-muted">
            {lastRefreshedAt ? `更新于 ${lastRefreshedAt.toLocaleTimeString('zh-CN', { hour12: false })}` : '正在读取'} · 每 2 秒刷新
          </span>
          <Button variant="secondary" size="sm" onClick={refresh} disabled={refreshing}>
            <RefreshCw className={`mr-2 h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
            {refreshing ? '刷新中' : '立即刷新'}
          </Button>
          <span className="rounded-full bg-[#FFF0E5] px-3 py-2 text-xs font-black text-primary">待处理 {pendingItems.length}</span>
        </div>
      </div>
      <div className="mb-4 flex flex-wrap gap-2">
        {[
          { key: 'pending' as const, label: '待处理', count: pendingItems.length },
          { key: 'resume' as const, label: '简历请求', count: resumeRequests.length },
          { key: 'follow_up' as const, label: '自动跟进', count: followUpRecords.length },
          { key: 'replied' as const, label: '已回复', count: repliedRecords.length },
        ].map(item => {
          const active = activeMonitorFilter === item.key
          return (
            <button
              key={item.key}
              type="button"
              onClick={() => setActiveMonitorFilter(item.key)}
              className={`rounded-full px-3 py-1 text-xs font-bold transition ${active ? 'bg-primary text-white' : 'border border-card-border text-muted hover:border-primary/60 hover:text-primary'}`}
            >
              {item.label} {item.count}
            </button>
          )
        })}
      </div>
      {notice && <div className="mb-3 rounded-2xl bg-[#FFF0E5] px-4 py-3 text-sm text-primary">{notice}</div>}
      <div className="space-y-3">
        {displayedHistory.map((item, index) => {
          const canReply = item.action === 'reply_pending'
          const isFollowUp = item.action === 'follow_up_sent'
          const isDetectedReply = item.action === 'hr_reply_detected'
          const isResumeFailure = item.action === 'resume_failed'
          const isResumeRequest = item.action === 'needs_resume' || isResumeFailure
          const isReplied = isOutboundReplyRecord(item)
          const parsed = parseHistoryDetail(item)
          const hasGeneratedReply = !isDetectedReply && Boolean(parsed.aiReply)
          const detectedReplyPreview = isDetectedReply ? item.detail.replace(/^HR回复:\s*/, '') : ''
          const conversationMessages = monitorConversationMessages(item, history)
          const showReplyContent = canReply || Boolean(parsed.hrQuestion) || hasGeneratedReply || isResumeRequest || isReplied || conversationMessages.length > 0
          const systemFailureReason = parsed.systemReason || (isResumeFailure ? '未获得更具体的错误信息，请查看运行日志。' : '')
          const targetUrl = monitorChatUrl(item)
          const openingChat = openingChatId === item.id
          const preparingReply = preparingReplyId === item.id
          const canOpenChat = (item.source_platform || 'boss') === 'boss' ? Boolean(item.id) : Boolean(targetUrl)
          return (
            <div key={item.id || `${item.created_at}-${index}`} className="grid gap-3 rounded-2xl border border-card-border bg-[#FFFCFA] p-4 lg:grid-cols-[130px_1fr_160px]">
              <div className="text-xs text-muted">
                <div>{item.created_at}</div>
                <div className="mt-2 rounded-full bg-white px-2 py-1 text-center font-bold text-primary">{getActionLabel(item.action)}</div>
              </div>
              <div>
                <div className="font-black">{item.company || '岗位'}｜{item.title || '监测记录'}</div>
                {isDetectedReply ? (
                  <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-muted">{detectedReplyPreview}</p>
                ) : showReplyContent ? (
                  <div className="mt-3 space-y-3">
                    {isFollowUp && (
                      <div>
                        <div className="text-xs font-black text-primary">自动跟进说明</div>
                        <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-muted">
                          HR 超过设定时间未回复，系统已自动执行一次跟进。
                        </p>
                      </div>
                    )}
                    {conversationMessages.length > 0 && (
                      <div className="overflow-hidden rounded-2xl border border-card-border bg-white">
                        <div className="flex items-center justify-between border-b border-card-border px-3 py-1.5">
                          <span className="text-xs font-black text-foreground">聊天记录</span>
                        </div>
                        <div className="max-h-[260px] divide-y divide-card-border overflow-y-auto overscroll-contain">
                          {conversationMessages.map((message, messageIndex) => {
                            const fromHr = message.sender === 'hr'
                            return (
                              <div key={`${item.id}-${messageIndex}-${message.sender}`} className="grid grid-cols-[28px_minmax(0,1fr)] items-start gap-2 px-3 py-1.5">
                                <div className={`flex h-7 w-7 items-center justify-center rounded-full text-[10px] font-black ${fromHr ? 'bg-[#FFF0E5] text-primary' : 'bg-emerald-50 text-emerald-700'}`}>
                                  {fromHr ? 'HR' : 'AI'}
                                </div>
                                <p className="min-w-0 whitespace-pre-wrap break-words text-[13px] leading-5 text-foreground">{message.text}</p>
                              </div>
                            )
                          })}
                        </div>
                      </div>
                    )}
                    {isResumeFailure && (
                      <div className="rounded-2xl border border-danger/30 bg-red-50 p-3">
                        <div className="text-xs font-black text-danger">系统失败原因</div>
                        <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-danger">{systemFailureReason}</p>
                      </div>
                    )}
                    {canReply ? (
                      <div>
                        <div className="mb-1 text-xs font-black text-primary">AI 建议回复（尚未回答）</div>
                        <textarea
                          id={`reply-draft-${item.id}`}
                          value={draftFor(item)}
                          onChange={event => setReplyDrafts(prev => ({ ...prev, [item.id]: event.target.value }))}
                          className="min-h-[92px] w-full rounded-2xl border border-card-border bg-white p-3 text-sm leading-6 text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                        />
                      </div>
                    ) : null}
                  </div>
                ) : (
                  <p className="mt-2 text-sm leading-6 text-muted">{item.detail || getActionLabel(item.action)}</p>
                )}
                {isDetectedReply ? (
                  <p className="mt-2 text-xs text-primary">已检测到 HR 新消息，等待继续读取完整对话并生成处理结果。</p>
                ) : canReply ? (
                  <p className="mt-2 text-xs text-primary">AI 建议：需要人工确认后再回复。</p>
                ) : item.action === 'needs_resume' ? (
                  <p className="mt-2 text-xs text-primary">简历请求：监测发现 HR 要简历，已生成定制简历，等待手动发送。</p>
                ) : item.action === 'resume_sent' ? (
                  <p className="mt-2 text-xs text-primary">已回复：定制简历已发送，本轮聊天记录保留在上方。</p>
                ) : isResumeFailure ? (
                  <p className="mt-2 text-xs text-danger">待处理：定制简历生成失败，尚无可下载文件，请手动处理或稍后重试生成。</p>
                ) : isReplied ? (
                  <p className="mt-2 text-xs text-primary">已回答：本轮 HR 与 AI 回复已保留在上方聊天记录中。</p>
                ) : null}
              </div>
              <div className="grid gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={!canOpenChat || openingChat}
                  onClick={() => void openMonitorConversation(item)}
                >
                  <ExternalLink className="mr-2 h-4 w-4" />{openingChat ? '定位中…' : monitorLinkLabel(item)}
                </Button>
                {isDetectedReply ? (
                  <Button size="sm" disabled={preparingReply} onClick={() => void prepareDetectedReply(item)}>
                    {preparingReply ? '读取中…' : '读取并生成建议'}
                  </Button>
                ) : canReply ? (
                  <>
                    <Button size="sm" onClick={() => sendManualReply(item)}><MessageCircle className="mr-2 h-4 w-4" />确认回复</Button>
                    <Button variant="secondary" size="sm" onClick={() => document.getElementById(`reply-draft-${item.id}`)?.focus()}>编辑回复</Button>
                    <Button variant="secondary" size="sm" onClick={() => dismissPendingReply(item)}>放弃</Button>
                  </>
                ) : (
                  <div className="px-2 py-1 text-center text-xs text-muted">本轮已处理，无需再次确认</div>
                )}
              </div>
            </div>
          )
        })}
        {!visibleHistory.length && (
          <div className="rounded-2xl border border-dashed border-card-border bg-[#FFFCFA] p-5 text-sm text-muted">
            {activeMonitorFilter === 'replied' ? '暂无近 7 天已回复对话。' : '暂无待处理 HR 问题。'}
          </div>
        )}
      </div>
    </div>
  )
}
