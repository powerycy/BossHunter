import { useLocation } from 'react-router-dom'
import { Activity, CircleAlert, CircleCheck, CircleX } from 'lucide-react'
import { useEffect, useState } from 'react'

const pageTitles: Record<string, string> = {
  '/': '工作台',
  '/jobs': '岗位池',
  '/monitor': '监测执行',
  '/config': '配置',
}

type BossLoginStatus = 'checking' | 'logged_in' | 'logged_out' | 'unavailable'

const loginStatusDisplay: Record<BossLoginStatus, { label: string; className: string; Icon: typeof Activity }> = {
  checking: { label: '正在检查 BOSS 登录', className: 'text-muted', Icon: Activity },
  logged_in: { label: 'BOSS 已登录', className: 'text-success', Icon: CircleCheck },
  logged_out: { label: 'BOSS 未登录', className: 'text-amber-600', Icon: CircleAlert },
  unavailable: { label: 'BOSS 连接不可用', className: 'text-red-500', Icon: CircleX },
}

export function Header() {
  const location = useLocation()
  const title = pageTitles[location.pathname] || 'BossHunter'
  const [bossLoginStatus, setBossLoginStatus] = useState<BossLoginStatus>('checking')

  useEffect(() => {
    let active = true
    const refreshLoginStatus = async () => {
      try {
        const response = await fetch('/api/browser/login-status', { cache: 'no-store' })
        const data = await response.json()
        if (!active) return
        const status = data.status as BossLoginStatus
        setBossLoginStatus(status in loginStatusDisplay ? status : 'unavailable')
      } catch {
        if (active) setBossLoginStatus('unavailable')
      }
    }

    refreshLoginStatus()
    const interval = window.setInterval(refreshLoginStatus, 60000)
    return () => {
      active = false
      window.clearInterval(interval)
    }
  }, [])

  const loginDisplay = loginStatusDisplay[bossLoginStatus]
  const LoginIcon = loginDisplay.Icon

  return (
    <header className="h-16 border-b border-card-border bg-[#FFFCFA] flex items-center justify-between px-6">
      <h1 className="text-lg font-black text-foreground">{title}</h1>
      <div className="flex items-center gap-4 text-xs text-muted">
        <span className="flex items-center gap-2">
          <Activity className="w-3 h-3 text-success" />
          本地服务运行中
        </span>
        <span className={`flex items-center gap-2 ${loginDisplay.className}`}>
          <LoginIcon className="w-3 h-3" />
          {loginDisplay.label}
        </span>
      </div>
    </header>
  )
}
