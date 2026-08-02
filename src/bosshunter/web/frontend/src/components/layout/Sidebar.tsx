import { NavLink } from 'react-router-dom'
import { BriefcaseBusiness, Github, LayoutDashboard, Radar, Settings } from 'lucide-react'
import { useEffect, useState } from 'react'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: '工作台' },
  { to: '/jobs', icon: BriefcaseBusiness, label: '岗位池' },
  { to: '/monitor', icon: Radar, label: '监测执行' },
  { to: '/config', icon: Settings, label: '配置' },
]

const GITHUB_URL = 'https://github.com/powerycy/BossHunter'

interface SidebarProps {
  pendingReplies?: number
}

export function Sidebar({ pendingReplies: pendingRepliesProp }: SidebarProps) {
  const [pendingReplies, setPendingReplies] = useState(pendingRepliesProp ?? 0)

  useEffect(() => {
    if (pendingRepliesProp !== undefined) {
      setPendingReplies(pendingRepliesProp)
      return
    }

    const fetchPendingReplies = async () => {
      try {
        const res = await fetch('/api/history/unresolved-replies/count')
        const data = await res.json()
        setPendingReplies(Number(data.count) || 0)
      } catch {
        setPendingReplies(0)
      }
    }

    fetchPendingReplies()
    const interval = setInterval(fetchPendingReplies, 30000)
    return () => clearInterval(interval)
  }, [pendingRepliesProp])

  return (
    <aside className="w-60 border-r border-card-border bg-white flex flex-col">
      <div className="h-16 flex items-center px-5 border-b border-card-border">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-2xl bg-primary text-white flex items-center justify-center shadow-lg shadow-primary/20">
            <span className="font-black text-sm">BH</span>
          </div>
          <div>
            <div className="font-black text-sm tracking-tight text-foreground">BossHunter</div>
            <div className="text-[11px] text-muted">v2.2 · 本地控制台</div>
          </div>
        </div>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-1">
        {navItems.map(item => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex items-center justify-between gap-3 px-3 py-3 rounded-xl text-sm transition-colors ${
                isActive
                  ? 'bg-[#FFF0E5] text-primary font-black'
                  : 'text-muted hover:text-foreground hover:bg-[#FFFCFA]'
              }`
            }
          >
            <span className="flex items-center gap-3">
              <item.icon className="w-4 h-4" />
              {item.label}
            </span>
            {item.to === '/monitor' && pendingReplies > 0 && (
              <span className="h-2 w-2 rounded-full bg-danger" aria-label="有待处理事项" />
            )}
          </NavLink>
        ))}
      </nav>

      <div className="px-4 py-4 border-t border-card-border space-y-3">
        <a
          href={GITHUB_URL}
          target="_blank"
          rel="noreferrer"
          className="relative flex items-center rounded-2xl border border-card-border bg-[#FFFCFA] px-3 py-3 text-xs font-black text-foreground transition-colors hover:border-primary/60 hover:text-primary"
        >
          <Github className="absolute left-3 h-4 w-4" />
          <span className="mx-auto flex items-center justify-center gap-2">
            <span className="text-xl leading-none text-yellow-400">★</span>
            BossHunter
          </span>
        </a>
        <p className="text-center text-[11px] leading-5 text-muted">❤️  欢迎点 Star 支持维护  ❤️</p>
      </div>
    </aside>
  )
}
