import { useEffect, useMemo, useState } from 'react'
import { useDashboard, type HistoryItem, type Job, type WorkbenchTask } from '@/hooks/useDashboard'
import { Button } from '@/components/ui/button'
import { JobsTable } from '@/components/dashboard/JobsTable'
import { RecycleBinPanel } from '@/components/dashboard/RecycleBinPanel'
import { ScoreJobsDialog } from '@/components/dashboard/ScoreJobsDialog'
import { parseHistoryDetail } from '@/lib/historyDetail'
import { getActionLabel, getStatusLabel } from '@/lib/status'
import {
  AlertTriangle,
  BriefcaseBusiness,
  Download,
  ExternalLink,
  Eye,
  MessageCircle,
  Play,
  RefreshCw,
  Square,
  Trash2,
  XCircle,
} from 'lucide-react'

type WorkbenchMode = 'full' | 'collect' | 'score' | 'rescore' | 'greet' | 'monitor'
type DashboardView = 'workbench' | 'jobs' | 'monitor'

const TASK_STAGE_LABELS = [
  '开始采集岗位',
  '开始 AI 评分',
  '开始重新评分',
  '开始单独 AI 评分',
  'AI 评分进度',
  '等待前端确认投递',
  '发送失败待处理',
  '执行一轮监测',
  '本轮监测完成，30 分钟后再次检查',
]

function currentTaskStage(task?: WorkbenchTask | null) {
  if (task?.stage) return task.stage
  const logs = task?.logs || []
  for (const log of logs.slice().reverse()) {
    if (log.includes('AI 评分进度')) return log
    const stage = TASK_STAGE_LABELS.find(label => log.includes(label))
    if (stage) return stage
  }
  return '等待后端返回阶段'
}

function taskStatusText(status: string) {
  if (status === 'failed') return '运行失败'
  if (status === 'paused') return '已暂停'
  if (status === 'completed_with_errors') return '完成但有失败'
  if (status === 'completed') return '已结束'
  if (status === 'stopped') return '已停止'
  if (status === 'stopping') return '停止中'
  if (status === 'pausing') return '暂停中'
  return '运行中'
}

function taskStatusClass(status: string) {
  if (status === 'failed' || status === 'paused' || status === 'completed_with_errors') return 'border-red-100 bg-red-50'
  if (status === 'completed' || status === 'stopped') return 'border-card-border bg-white'
  return 'border-primary/20 bg-[#FFF0E5]'
}

function taskStatusTitle(status: string) {
  if (status === 'completed' || status === 'stopped') return '最近任务状态'
  return '当前阶段'
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
    description: '只采集岗位并保存链接与组合进度，不自动评分、发送或监测。',
  },
  {
    mode: 'score',
    title: '单独 AI 评分',
    description: '复用已有岗位池，按范围和数量继续评分，不重新采集岗位。',
  },
  {
    mode: 'monitor',
    title: '单独监测',
    description: '只监测过往已投递项目；发现 HR 要简历或问题后进入对应处理。',
  },
]

const statItems = [
  { label: '采集总数', key: '采集总数' },
  { label: '初筛通过', key: '初筛通过', highlight: true },
  { label: 'AI评分', key: 'AI评分' },
  { label: '人工确认', key: '人工确认', highlight: true },
  { label: '简历生成', key: '简历生成', highlight: true },
]

function jobSubtitle(job: Job) {
  return [job.score ? `匹配 ${job.score}` : '', job.salary, getStatusLabel(job.status)].filter(Boolean).join(' · ')
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
  const { workbench, jobs, history, loading, error, refresh, startTask, stopTask, pauseTask, resumeTask, endTask } = useDashboard(view === 'jobs' ? 'all' : view)
  const [selected, setSelected] = useState<string[]>([])
  const [notice, setNotice] = useState('')
  const [preflightChecks, setPreflightChecks] = useState<PreflightCheck[]>([])
  const [preflightMode, setPreflightMode] = useState<WorkbenchMode>('full')
  const [selectedJob, setSelectedJob] = useState<Job | null>(null)
  const [modePending, setModePending] = useState<WorkbenchMode | null>(null)
  const [confirmedDeliveryIds, setConfirmedDeliveryIds] = useState<Set<string>>(new Set())
  const [jobPoolSelectedIds, setJobPoolSelectedIds] = useState<string[]>([])
  const [scoreDialogOpen, setScoreDialogOpen] = useState(false)
  const [conflictMode, setConflictMode] = useState<WorkbenchMode | null>(null)
  const [greetingRetrySelected, setGreetingRetrySelected] = useState<string[]>([])

  const todayJobs = useMemo(
    () => workbench.pending_confirmation.filter(job => !confirmedDeliveryIds.has(job.id)),
    [workbench.pending_confirmation, confirmedDeliveryIds]
  )
  const pendingGreetingJobs = useMemo(
    () => workbench.pending_greetings.filter(job => !confirmedDeliveryIds.has(job.id)),
    [workbench.pending_greetings, confirmedDeliveryIds]
  )
  const greetingFailureJobs = workbench.greeting_generation_items || []
  const activeTask = workbench.task
  const visibleTask = activeTask || workbench.last_task
  const visibleTaskError = visibleTask?.error ? taskErrorFeedback(visibleTask.error) : null
  const pendingReplies = history.filter(item => item.action === 'reply_pending')

  const toggleJob = (id: string) => {
    setSelected(prev => (prev.includes(id) ? prev.filter(item => item !== id) : [...prev, id]))
  }

  const runPreflight = async (mode: WorkbenchMode) => {
    setPreflightMode(mode)
    const res = await fetch(`/api/workbench/preflight?mode=${mode}`)
    const data = await parsePreflightResponse(res)
    setPreflightChecks(data.checks)
    if (!data.ok) {
      setNotice('请按提示处理后再启动')
      return false
    }
    return true
  }

  const handleModeClick = async (mode: WorkbenchMode) => {
    if (modePending) return
    if (mode === 'score') {
      if (activeTask?.status === 'paused') {
        setConflictMode(mode)
        return
      }
      if (activeTask && activeTask.mode !== 'score') {
        setNotice(`当前正在运行${activeTask.label}，请先停止后再开始评分。`)
        return
      }
      setScoreDialogOpen(true)
      return
    }
    try {
      if (activeTask?.status === 'paused') {
        setConflictMode(mode)
        return
      }
      if (activeTask?.mode === mode) {
        if (window.confirm(`是否停止当前${activeTask.label}任务？已入库岗位会保留。`)) {
          setModePending(mode)
          setNotice(`正在停止${activeTask.label}...`)
          await stopTask(activeTask.id)
          setNotice(`${activeTask.label}已请求停止。`)
        }
        return
      }
      if (activeTask) {
        setNotice(
          activeTask.status === 'stopping'
            ? `当前${activeTask.label}正在停止，请等待后台完全结束后再启动其他模式。`
            : `当前正在运行${activeTask.label}，请先点击橙色卡片停止后再启动其他模式。`
        )
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

  const startAfterEndingConflict = async () => {
    if (!conflictMode || !activeTask) return
    const nextMode = conflictMode
    try {
      await endTask(activeTask.id)
      setConflictMode(null)
      setModePending(nextMode)
      if (!(await runPreflight(nextMode))) return
      await startTask(nextMode)
      setNotice('旧任务已结束，新任务已启动。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '启动新任务失败')
    } finally {
      setModePending(null)
    }
  }

  const resumeOldTask = async () => {
    if (!activeTask) return
    try {
      await resumeTask(activeTask.id)
      setConflictMode(null)
      setNotice('已继续旧任务；恢复会复用原非秘密配置快照。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '恢复旧任务失败')
    }
  }

  const regenerateGreetings = async (ids: string[]) => {
    if (!ids.length) return
    if (!window.confirm(`确认重新生成 ${ids.length} 个失败岗位的招呼语？成功后仍需再次人工确认发送。`)) return
    try {
      await startTask('greet', { job_ids: ids, force_regenerate: false })
      setGreetingRetrySelected([])
      setNotice('招呼语重新生成任务已启动。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '重新生成失败')
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
      if (!ids.some(id => workbench.send_errors.some(job => job.id === id))) {
        setConfirmedDeliveryIds(prev => new Set([...prev, ...ids]))
      }
      await refresh()
      setNotice(`已确认投递 ${count} 个岗位，后端会按队列推进。`)
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
      await refresh()
      setNotice(`已直接进入发送流程 ${count} 个岗位。`)
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

  const overrideFilteredJob = async (job: Job) => {
    if (!window.confirm(`该岗位因“${job.filter_reason || '岗位偏好规则'}”被过滤。确认忽略该偏好并进入待人工确认列表吗？验证码、账号异常、平台拦截和频率限制不支持覆盖。`)) return
    try {
      const res = await fetch(`/api/jobs/${job.id}/override-filter`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirmed: true }) })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(data.error || '人工推进失败')
      await refresh()
      setNotice('岗位已人工推进，后续仍需人工确认。')
    } catch (err) {
      setNotice(err instanceof Error ? err.message : '人工推进失败')
    }
  }

  const startScoreTask = async (options: { scope: 'pending' | 'failed' | 'selected' | 'all_scored'; limit: number | null; job_ids: string[]; force_rescore: boolean }) => {
    await startTask('score', options)
    await refresh()
    setNotice('单独 AI 评分已启动，任务状态会在下方更新。')
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
    return (
      <>
        <JobsPoolView
          jobs={jobs}
          selectedIds={jobPoolSelectedIds}
          onToggleSelected={id => setJobPoolSelectedIds(prev => prev.includes(id) ? prev.filter(item => item !== id) : [...prev, id])}
          onSelectAll={ids => setJobPoolSelectedIds(ids)}
          onOpenScore={() => setScoreDialogOpen(true)}
          onNotice={setNotice}
          onOverrideFilter={overrideFilteredJob}
          onRefresh={refresh}
        />
        <ScoreJobsDialog
          open={scoreDialogOpen}
          selectedJobIds={jobPoolSelectedIds}
          activeTask={activeTask}
          onClose={() => setScoreDialogOpen(false)}
          onStart={startScoreTask}
        />
        {notice && <div className="fixed bottom-5 right-5 z-[60] rounded-2xl bg-foreground px-4 py-3 text-sm text-white shadow-xl">{notice}</div>}
      </>
    )
  }

  if (view === 'monitor') {
    return <MonitorExecutionView history={history} refresh={refresh} />
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
            <Button variant="secondary" size="sm" onClick={refresh}>
              <RefreshCw className="mr-2 h-4 w-4" />
              刷新
            </Button>
            <span className="rounded-full bg-[#FFF0E5] px-3 py-2 text-xs font-black text-primary">
              {activeTask ? `${activeTask.label}中` : '当前空闲'}
            </span>
          </div>
        </div>


        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
          {modes.map(item => {
            const isActive = activeTask?.mode === item.mode
            const disabled = Boolean(activeTask && !isActive)
            return (
              <button
                key={item.mode}
                onClick={() => handleModeClick(item.mode)}
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
                  <div className="text-lg font-black">{modePending === item.mode ? '任务启动中' : isActive ? `${item.title}中` : item.title}</div>
                  {isActive ? <Square className="h-5 w-5 fill-current" /> : <Play className="h-5 w-5" />}
                </div>
                <p className={`text-xs leading-6 ${isActive ? 'text-white/85' : 'text-muted'}`}>{item.description}</p>
              </button>
            )
          })}
        </div>
        {notice && <div className="mt-3 rounded-2xl bg-[#FFF0E5] px-4 py-3 text-sm text-primary">{notice}</div>}
        {preflightChecks.some(check => check.status !== 'pass') && (
          <PreflightPanel checks={preflightChecks} checking={Boolean(modePending)} onRetry={retryPreflight} />
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
              <div className="mt-1 text-lg font-black text-foreground">{currentTaskStage(visibleTask)}</div>
              <div className="mt-1 text-xs font-bold text-muted">任务状态：{taskStatusText(visibleTask.status)}</div>
              {visibleTask.deadline_at && (
                <div className="mt-1 text-xs font-bold text-muted">
                  自动截止：{new Date(visibleTask.deadline_at).toLocaleString('zh-CN', { hour12: false })}
                </div>
              )}
            </div>
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
            {visibleTask.stop_reason && <div className="mt-3 rounded-2xl bg-[#FFF0E5] px-3 py-2 text-sm text-primary">{visibleTask.stop_reason}</div>}
            {activeTask && activeTask.status === 'running' && <div className="mt-3 flex gap-2"><Button variant="secondary" size="sm" onClick={() => pauseTask(activeTask.id)}>暂停任务</Button><Button variant="secondary" size="sm" onClick={() => stopTask(activeTask.id)}>停止任务</Button></div>}
            {activeTask && activeTask.status === 'pausing' && <div className="mt-3 text-xs font-bold text-primary">正在保存当前检查点，稍后可继续。</div>}
            {activeTask && activeTask.status === 'paused' && <div className="mt-3 flex flex-wrap items-center gap-2"><Button size="sm" onClick={resumeOldTask}>继续旧任务</Button><Button variant="secondary" size="sm" onClick={startAfterEndingConflict} disabled={!conflictMode}>结束旧任务并开始新任务</Button><Button variant="ghost" size="sm" onClick={() => setConflictMode(null)}>取消</Button></div>}
            {conflictMode && activeTask?.status === 'paused' && <p className="mt-2 text-xs text-primary">已选择“{modes.find(item => item.mode === conflictMode)?.title || conflictMode}”。请选择继续旧任务、结束旧任务并开始新任务，或取消。</p>}
          </div>
        )}
      </section>

      <section className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        {statItems.map(item => (
          <div key={item.label} className="rounded-2xl border border-card-border bg-white p-4">
            <div className="text-xs text-muted">{item.label}</div>
            <div className={`mt-1 text-2xl font-black ${item.highlight ? 'text-primary' : 'text-foreground'}`}>
              {workbench.funnel[item.key] || 0}
            </div>
          </div>
        ))}
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

      {greetingFailureJobs.length > 0 && (
        <section className="rounded-3xl border border-amber-200 bg-amber-50 p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-4"><div><h3 className="text-lg font-black text-amber-800">招呼语生成失败 / 待生成</h3><p className="mt-1 text-xs text-amber-800/80">岗位不会消失；失败原因已逐条保存，可单条或批量重新生成。成功后仍需人工确认发送。</p></div><div className="flex gap-2"><Button size="sm" disabled={!greetingRetrySelected.length} onClick={() => regenerateGreetings(greetingRetrySelected)}>批量重新生成 {greetingRetrySelected.length} 个</Button><Button variant="secondary" size="sm" onClick={() => regenerateGreetings(greetingFailureJobs.map(job => job.id))}>全部重新生成</Button></div></div>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">{greetingFailureJobs.map(job => { let failure: { user_message?: string; at?: string; attempts?: number } = {}; try { failure = job.greeting_failure_json ? JSON.parse(job.greeting_failure_json) : {} } catch { /* redacted server text remains visible */ } return <div key={job.id} className="rounded-2xl border border-amber-200 bg-white p-4"><div className="flex items-start gap-3"><input type="checkbox" checked={greetingRetrySelected.includes(job.id)} onChange={() => setGreetingRetrySelected(prev => prev.includes(job.id) ? prev.filter(id => id !== job.id) : [...prev, job.id])} className="mt-1 h-4 w-4 accent-primary" aria-label={`选择重新生成 ${job.company} ${job.title}`} /><div className="min-w-0 flex-1"><div className="font-black">{job.company}｜{job.title}</div><p className="mt-1 text-xs text-danger">{failure.user_message || '尚未生成招呼语'}</p><p className="mt-1 text-[11px] text-muted">尝试 {failure.attempts || job.greeting_attempts || 0} 次{failure.at ? ` · ${failure.at}` : ''}</p></div></div><div className="mt-3 flex gap-2"><Button size="sm" onClick={() => regenerateGreetings([job.id])}>单条重新生成</Button><Button variant="secondary" size="sm" disabled={!job.url} onClick={() => window.open(job.url, '_blank', 'noopener,noreferrer')}><ExternalLink className="mr-2 h-4 w-4" />打开原岗位</Button></div></div> })}</div>
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
            <Button variant="secondary" size="sm" onClick={() => setSelected(todayJobs.map(job => job.id))}>全选</Button>
            <Button variant="secondary" size="sm" onClick={() => setSelected([])}>清空</Button>
            <Button variant="secondary" size="sm" onClick={() => rejectSelectedJobs(selected)}>放弃已选 {selected.length} 个</Button>
            <Button size="sm" onClick={() => confirmDeliver(selected)}>一键投递已选 {selected.length} 个</Button>
          </div>
        </div>
        {todayJobs.length ? (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {todayJobs.map(job => (
              <JobActionCard
                key={job.id}
                job={job}
                selected={selected.includes(job.id)}
                onToggle={() => toggleJob(job.id)}
                onDetail={() => openJobDetail(job)}
              />
            ))}
          </div>
        ) : (
          <div className="rounded-2xl border border-dashed border-card-border bg-[#FFFCFA] p-5 text-sm text-muted">今天暂时没有待确认岗位。</div>
        )}
      </section>

      {selectedJob && <JobDetailModal job={selectedJob} onClose={() => setSelectedJob(null)} />}
      <ScoreJobsDialog
        open={scoreDialogOpen}
        selectedJobIds={jobPoolSelectedIds}
        activeTask={activeTask}
        onClose={() => setScoreDialogOpen(false)}
        onStart={startScoreTask}
      />
    </div>
  )
}

function JobActionCard({ job, selected, onToggle, onDetail }: { job: Job; selected: boolean; onToggle: () => void; onDetail: () => void }) {
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
      <div className="mt-3 flex gap-2">
        <Button variant="secondary" size="sm" onClick={onDetail}><Eye className="mr-2 h-4 w-4" />查看详情</Button>
        <Button variant="secondary" size="sm" disabled={!job.url} onClick={() => window.open(job.url, '_blank', 'noopener,noreferrer')}><ExternalLink className="mr-2 h-4 w-4" />跳转岗位链接</Button>
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
          <InfoBlock label="公司" value={[job.company_size, job.company_industry].filter(Boolean).join(' · ') || '-'} />
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

function JobsPoolView({
  jobs,
  selectedIds,
  onToggleSelected,
  onSelectAll,
  onOpenScore,
  onNotice,
  onOverrideFilter,
  onRefresh,
}: {
  jobs: Job[]
  selectedIds: string[]
  onToggleSelected: (id: string) => void
  onSelectAll: (ids: string[]) => void
  onOpenScore: () => void
  onNotice: (message: string) => void
  onOverrideFilter: (job: Job) => void
  onRefresh: () => Promise<void>
}) {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [city, setCity] = useState('')
  const [showRecycleBin, setShowRecycleBin] = useState(false)
  const [recycleJobs, setRecycleJobs] = useState<Job[]>([])
  const [recycleSelectedIds, setRecycleSelectedIds] = useState<string[]>([])
  const [recycleLoading, setRecycleLoading] = useState(false)
  const [permanentDeleteIds, setPermanentDeleteIds] = useState<string[]>([])
  const [permanentDeleteAcknowledged, setPermanentDeleteAcknowledged] = useState(false)
  const filteredJobs = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return jobs.filter(job => {
      const matchesQuery = !needle || [job.company, job.title, job.jd].some(value => String(value || '').toLowerCase().includes(needle))
      return matchesQuery && (!status || job.status === status) && (!city || job.city === city)
    })
  }, [jobs, query, status, city])
  const allVisibleSelected = filteredJobs.length > 0 && filteredJobs.every(job => selectedIds.includes(job.id))

  const loadRecycleBin = async () => {
    setRecycleLoading(true)
    try {
      const pageSize = 200
      const collected: Job[] = []
      let offset = 0
      let total: number | null = null
      while (true) {
        const res = await fetch(`/api/jobs?deleted=only&limit=${pageSize}&offset=${offset}`, { cache: 'no-store' })
        if (!res.ok) throw new Error(`回收站接口返回 ${res.status}`)
        const page = await res.json()
        if (!Array.isArray(page)) throw new Error('回收站响应格式无效')
        collected.push(...page)
        const headerTotal = Number(res.headers.get('X-Total-Count'))
        if (Number.isFinite(headerTotal)) total = headerTotal
        if (!page.length || (total !== null && collected.length >= total) || page.length < pageSize) break
        offset += page.length
      }
      const unique = new Map<string, Job>()
      collected.forEach(job => unique.set(String(job.id), job))
      setRecycleJobs([...unique.values()])
      setRecycleSelectedIds(previous => previous.filter(id => unique.has(id)))
    } catch (err) {
      onNotice(err instanceof Error ? err.message : '读取回收站失败')
    } finally {
      setRecycleLoading(false)
    }
  }

  useEffect(() => {
    void loadRecycleBin()
  }, [])

  const actionError = async (res: Response, fallback: string) => {
    const data = await res.json().catch(() => ({}))
    const blocked = Array.isArray(data.blocked)
      ? data.blocked.map((item: { job_id?: string; reasons?: string[] }) => `${item.job_id || '岗位'}：${(item.reasons || []).join('、')}`).join('；')
      : ''
    const notFound = Array.isArray(data.not_found) && data.not_found.length ? `不存在：${data.not_found.join('、')}` : ''
    throw new Error([data.error || fallback, blocked, notFound].filter(Boolean).join('；'))
  }

  const postJobAction = async (path: string, payload: Record<string, unknown>) => {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) await actionError(res, '岗位操作失败')
    return res.json()
  }

  const protectedStatus = (job: Job) => ['sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent'].includes(job.status)

  const softDelete = async (jobIds: string[], jobsForWarning: Job[]) => {
    if (!jobIds.length) return
    const hasHistoryRisk = jobsForWarning.some(protectedStatus)
    const prompt = hasHistoryRisk
      ? `选中岗位中包含已发送、已回复或已跟进岗位。确认仅将 ${jobIds.length} 个岗位移入回收站并保留全部历史吗？`
      : `确认将 ${jobIds.length} 个岗位移入回收站吗？岗位不会永久删除。`
    if (!window.confirm(prompt)) return
    try {
      const result = await postJobAction('/api/jobs/soft-delete', { job_ids: jobIds, confirmed: true })
      const affected = new Set(jobIds)
      setRecycleSelectedIds(previous => previous.filter(id => !affected.has(id)))
      onSelectAll(selectedIds.filter(id => !affected.has(id)))
      await onRefresh()
      await loadRecycleBin()
      onNotice(`已移入回收站 ${result.affected_count || 0} 条岗位。`)
    } catch (err) {
      onNotice(err instanceof Error ? err.message : '移入回收站失败')
    }
  }

  const restore = async (jobIds: string[]) => {
    if (!jobIds.length || !window.confirm(`确认恢复 ${jobIds.length} 个岗位吗？不会自动评分、生成、发送或监测。`)) return
    try {
      const result = await postJobAction('/api/jobs/restore', { job_ids: jobIds, confirmed: true })
      setRecycleSelectedIds(previous => previous.filter(id => !jobIds.includes(id)))
      await onRefresh()
      await loadRecycleBin()
      onNotice(`已恢复 ${result.affected_count || 0} 条岗位。`)
    } catch (err) {
      onNotice(err instanceof Error ? err.message : '恢复失败')
    }
  }

  const permanentDelete = (jobIds: string[]) => {
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
      await loadRecycleBin()
      setPermanentDeleteIds([])
      setPermanentDeleteAcknowledged(false)
      onNotice(`已永久删除 ${result.affected_count || 0} 条岗位；此操作不可恢复。`)
    } catch (err) {
      onNotice(err instanceof Error ? err.message : '永久删除失败')
    }
  }

  const exportJobs = async (format: 'xlsx' | 'csv', scope: 'all' | 'filtered' | 'selected') => {
    try {
      const scopeIds = scope === 'selected' ? selectedIds : []
      const res = await fetch('/api/jobs/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          format,
          scope,
          job_ids: scopeIds,
          filters: scope === 'filtered'
            ? { q: query.trim(), city: city.trim(), status: status.trim() }
            : {},
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
      onNotice(`已导出 ${Number.isFinite(exportedCount) ? exportedCount : 0} 条岗位。`)
    } catch (err) {
      onNotice(err instanceof Error ? err.message : '导出失败')
    }
  }

  if (showRecycleBin) {
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <Button variant="ghost" size="sm" onClick={() => setShowRecycleBin(false)}>返回岗位池</Button>
          <Button variant="secondary" size="sm" onClick={() => void loadRecycleBin()} disabled={recycleLoading}>刷新回收站</Button>
        </div>
        <RecycleBinPanel
          jobs={recycleJobs}
          selectedIds={recycleSelectedIds}
          loading={recycleLoading}
          onToggleSelected={id => setRecycleSelectedIds(previous => previous.includes(id) ? previous.filter(item => item !== id) : [...previous, id])}
          onSelectAll={setRecycleSelectedIds}
          onRestore={job => void restore([job.id])}
          onPermanentDelete={job => void permanentDelete([job.id])}
          onBatchRestore={() => void restore(recycleSelectedIds)}
          onBatchPermanentDelete={() => void permanentDelete(recycleSelectedIds)}
        />
        {permanentDeleteIds.length > 0 && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4" role="dialog" aria-modal="true" aria-labelledby="permanent-delete-title">
            <div className="w-full max-w-lg rounded-3xl border border-red-200 bg-white p-6 shadow-2xl">
              <div className="flex items-start gap-3">
                <AlertTriangle className="mt-0.5 h-6 w-6 shrink-0 text-danger" />
                <div>
                  <h3 id="permanent-delete-title" className="text-xl font-black text-foreground">确认永久删除</h3>
                  <p className="mt-2 text-sm leading-6 text-muted">
                    将永久删除 {permanentDeleteIds.length} 条岗位及其关联历史，删除后无法恢复。已发送、已回复或存在历史证据的岗位仍会由后端拒绝删除。
                  </p>
                </div>
              </div>
              <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-2xl border border-red-100 bg-red-50 p-3 text-sm font-bold text-foreground">
                <input
                  type="checkbox"
                  checked={permanentDeleteAcknowledged}
                  onChange={event => setPermanentDeleteAcknowledged(event.target.checked)}
                  className="mt-0.5 h-4 w-4 accent-danger"
                />
                <span>我确认永久删除，且已了解此操作无法撤销。</span>
              </label>
              <div className="mt-6 flex justify-end gap-3">
                <Button variant="secondary" size="sm" onClick={() => { setPermanentDeleteIds([]); setPermanentDeleteAcknowledged(false) }}>
                  取消
                </Button>
                <Button variant="destructive" size="sm" disabled={!permanentDeleteAcknowledged} onClick={() => void confirmPermanentDelete()}>
                  永久删除
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="rounded-3xl border border-card-border bg-white p-5">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-black">岗位池</h2>
          <p className="mt-1 text-sm text-muted">已加载 {jobs.length} 条未删除岗位；当前筛选 {filteredJobs.length} 条，选择状态独立于投递确认。</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => { setShowRecycleBin(true); void loadRecycleBin() }}>
            <Trash2 className="mr-1 h-4 w-4" />回收站 ({recycleJobs.length})
          </Button>
          <BriefcaseBusiness className="h-6 w-6 text-primary" />
        </div>
      </div>
      <div className="mb-4 grid gap-2 md:grid-cols-[1fr_160px_160px_auto]">
        <input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索公司、职位或 JD" className="rounded-xl border border-card-border bg-[#FFFCFA] px-3 py-2 text-sm outline-none focus:border-primary" />
        <select value={status} onChange={event => setStatus(event.target.value)} className="rounded-xl border border-card-border bg-[#FFFCFA] px-3 py-2 text-sm outline-none focus:border-primary">
          <option value="">全部状态</option>
          {[...new Set(jobs.map(job => job.status).filter(Boolean))].map(value => <option key={value} value={value}>{getStatusLabel(value)}</option>)}
        </select>
        <select value={city} onChange={event => setCity(event.target.value)} className="rounded-xl border border-card-border bg-[#FFFCFA] px-3 py-2 text-sm outline-none focus:border-primary">
          <option value="">全部城市</option>
          {[...new Set(jobs.map(job => job.city).filter(Boolean))].sort().map(value => <option key={value} value={value}>{value}</option>)}
        </select>
        <Button variant="secondary" onClick={onOpenScore}>单独 AI 评分</Button>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2 text-xs">
        <Button variant="secondary" size="sm" onClick={() => onSelectAll(allVisibleSelected ? selectedIds.filter(id => !filteredJobs.some(job => job.id === id)) : [...new Set([...selectedIds, ...filteredJobs.map(job => job.id)])])}>{allVisibleSelected ? '取消全选当前筛选' : '全选当前筛选'}</Button>
        <span className="rounded-full bg-[#FFF0E5] px-3 py-2 font-bold text-primary">已选择 {selectedIds.length} 条</span>
        <Button variant="destructive" size="sm" disabled={!selectedIds.length} onClick={() => void softDelete(selectedIds, jobs.filter(job => selectedIds.includes(job.id)))}>批量移入回收站</Button>
        <ExportMenu onExport={exportJobs} hasSelection={selectedIds.length > 0} hasFiltered={filteredJobs.length > 0} />
      </div>
      <JobsTable jobs={filteredJobs} selectedIds={selectedIds} onToggleSelected={onToggleSelected} onOverrideFilter={onOverrideFilter} onSoftDelete={job => void softDelete([job.id], [job])} />
    </div>
  )
}

function ExportMenu({ onExport, hasSelection, hasFiltered }: { onExport: (format: 'xlsx' | 'csv', scope: 'all' | 'filtered' | 'selected') => void; hasSelection: boolean; hasFiltered: boolean }) {
  const [format, setFormat] = useState<'xlsx' | 'csv'>('xlsx')
  return (
    <div className="ml-auto flex flex-wrap items-center gap-2">
      <select value={format} onChange={event => setFormat(event.target.value as 'xlsx' | 'csv')} className="rounded-xl border border-card-border bg-white px-2 py-2 text-xs outline-none focus:border-primary"><option value="xlsx">XLSX</option><option value="csv">CSV</option></select>
      <Button variant="secondary" size="sm" disabled={!hasFiltered} onClick={() => onExport(format, 'filtered')}>导出筛选结果</Button>
      <Button variant="secondary" size="sm" disabled={!hasSelection} onClick={() => onExport(format, 'selected')}>导出所选岗位</Button>
      <Button variant="secondary" size="sm" onClick={() => onExport(format, 'all')}>导出全部岗位</Button>
    </div>
  )
}

type MonitorFilter = 'pending' | 'resume' | 'follow_up' | 'replied'
const REPLY_RESOLUTION_ACTIONS = ['reply_dismissed', 'replied', 'auto_replied']

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

function latestHrText(item: HistoryItem) {
  const parsed = parseHistoryDetail(item)
  const latestHr = [...parsed.conversationTail].reverse().find(message => message.sender === 'hr' && message.text.trim())
  return parsed.hrQuestion || latestHr?.text || ''
}

function MonitorExecutionView({ history, refresh }: { history: HistoryItem[]; refresh: () => Promise<void> }) {
  const pendingReplies = uniqueLatestByJob(history.filter(item =>
    item.action === 'reply_pending' && !isReplyPendingResolved(item, history)
  ))
  const resumeFailures = uniqueLatestByJob(history.filter(item =>
    item.action === 'resume_failed' && !isResumeFailureResolved(item, history)
  ))
  const pendingItems = uniqueLatestByJob(
    [...pendingReplies, ...resumeFailures].sort((left, right) => right.id - left.id)
  )
  const resumeRequests = uniqueLatestByJob(history.filter(item =>
    item.action === 'needs_resume' || item.action === 'resume_sent' || item.action === 'resume_failed'
  ))
  const resumeRequestJobIds = new Set(resumeRequests.map(item => item.job_id).filter(Boolean))
  const followUpRecords = uniqueLatestByJob(history.filter(item => item.action === 'follow_up_sent'))
  const repliedRecords = uniqueLatestByJob(history.filter(item =>
    (item.action === 'replied' || item.action === 'auto_replied')
      && !resumeRequestJobIds.has(item.job_id)
  ))
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

  return (
    <div className="rounded-3xl border border-card-border bg-white p-5">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-black">监测执行</h2>
          <p className="mt-1 text-sm text-muted">这里不启动监测，只处理监测发现的 HR 问题、回复建议和结果。</p>
        </div>
        <span className="rounded-full bg-[#FFF0E5] px-3 py-2 text-xs font-black text-primary">待处理 {pendingItems.length}</span>
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
          const isResumeFailure = item.action === 'resume_failed'
          const isResumeRequest = item.action === 'needs_resume' || item.action === 'resume_sent' || isResumeFailure
          const isReplied = item.action === 'replied' || item.action === 'auto_replied'
          const parsed = parseHistoryDetail(item)
          const hrText = latestHrText(item)
          const isLegacyReplied = item.action === 'replied' && parsed.schema === 'legacy_text'
          const hasGeneratedReply = Boolean(parsed.aiReply) && !isLegacyReplied
          const showReplyContent = canReply || Boolean(parsed.hrQuestion) || hasGeneratedReply || isResumeRequest || isReplied
          const aiReplyText = parsed.aiReply || item.detail || getActionLabel(item.action)
          const systemFailureReason = parsed.systemReason || (isResumeFailure ? '未获得更具体的错误信息，请查看运行日志。' : '')
          return (
            <div key={`${item.created_at}-${index}`} className="grid gap-3 rounded-2xl border border-card-border bg-[#FFFCFA] p-4 lg:grid-cols-[130px_1fr_160px]">
              <div className="text-xs text-muted">
                <div>{item.created_at}</div>
                <div className="mt-2 rounded-full bg-white px-2 py-1 text-center font-bold text-primary">{getActionLabel(item.action)}</div>
              </div>
              <div>
                <div className="font-black">{item.company || '岗位'}｜{item.title || '监测记录'}</div>
                {showReplyContent ? (
                  <div className="mt-3 space-y-3">
                    {(isFollowUp || hrText) && (
                      <div>
                        <div className="text-xs font-black text-primary">{isFollowUp ? '自动跟进说明' : '对方问题 / HR'}</div>
                        <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-muted">
                          {isFollowUp ? 'HR 超过设定时间未回复，系统已自动执行一次跟进。' : hrText}
                        </p>
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
                        <div className="mb-1 text-xs font-black text-primary">AI 建议回复</div>
                        <textarea
                          value={draftFor(item)}
                          onChange={event => setReplyDrafts(prev => ({ ...prev, [item.id]: event.target.value }))}
                          className="min-h-[92px] w-full rounded-2xl border border-card-border bg-white p-3 text-sm leading-6 text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                        />
                      </div>
                    ) : isResumeRequest || !hasGeneratedReply ? null : (
                      <div className="rounded-2xl border border-card-border bg-white p-3">
                        <div className="text-xs font-black text-primary">AI 回复</div>
                        <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-muted">{aiReplyText}</p>
                      </div>
                    )}
                  </div>
                ) : (
                  <p className="mt-2 text-sm leading-6 text-muted">{item.detail || getActionLabel(item.action)}</p>
                )}
                {canReply ? (
                  <p className="mt-2 text-xs text-primary">AI 建议：需要人工确认后再回复。</p>
                ) : item.action === 'needs_resume' ? (
                  <p className="mt-2 text-xs text-primary">简历请求：监测发现 HR 要简历，已生成定制简历，等待手动发送。</p>
                ) : item.action === 'resume_sent' ? (
                  <p className="mt-2 text-xs text-primary">简历生成：定制简历已生成，并已标记发送。</p>
                ) : isResumeFailure ? (
                  <p className="mt-2 text-xs text-danger">待处理：定制简历生成失败，尚无可下载文件，请手动处理或稍后重试生成。</p>
                ) : isReplied ? (
                  <p className="mt-2 text-xs text-primary">已回复：HR 已有反馈或系统已完成回复处理。</p>
                ) : null}
              </div>
              <div className="grid gap-2">
                <Button size="sm" disabled={!canReply} onClick={() => sendManualReply(item)}><MessageCircle className="mr-2 h-4 w-4" />确认回复</Button>
                <Button variant="secondary" size="sm" disabled={!canReply} onClick={() => setReplyDrafts(prev => ({ ...prev, [item.id]: draftFor(item) }))}>编辑回复</Button>
                <Button variant="secondary" size="sm" disabled={!canReply} onClick={() => dismissPendingReply(item)}>放弃</Button>
              </div>
            </div>
          )
        })}
        {!visibleHistory.length && <div className="rounded-2xl border border-dashed border-card-border bg-[#FFFCFA] p-5 text-sm text-muted">暂无待处理 HR 问题。</div>}
      </div>
    </div>
  )
}
