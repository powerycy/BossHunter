import { useLocation } from 'react-router-dom'
import { Activity } from 'lucide-react'

const pageTitles: Record<string, string> = {
  '/': 'Dashboard',
  '/config': '配置管理',
}

export function Header() {
  const location = useLocation()
  const title = pageTitles[location.pathname] || 'BossHunter'

  return (
    <header className="h-14 border-b border-zinc-800 flex items-center justify-between px-6">
      <h1 className="text-lg font-semibold">{title}</h1>
      <div className="flex items-center gap-2 text-xs text-zinc-400">
        <Activity className="w-3 h-3 text-success" />
        <span>服务运行中</span>
      </div>
    </header>
  )
}
