import { NavLink } from 'react-router-dom'
import { BarChart3, Settings } from 'lucide-react'

const navItems = [
  { to: '/', icon: BarChart3, label: '看板' },
  { to: '/config', icon: Settings, label: '配置' },
]

export function Sidebar() {
  return (
    <aside className="w-60 border-r border-zinc-800 bg-zinc-950 flex flex-col">
      {/* Logo */}
      <div className="h-14 flex items-center px-6 border-b border-zinc-800">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-primary/20 flex items-center justify-center">
            <span className="text-primary font-bold text-sm">BH</span>
          </div>
          <span className="font-semibold text-sm">BossHunter</span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4 space-y-1">
        {navItems.map(item => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors ${
                isActive
                  ? 'bg-zinc-800 text-white'
                  : 'text-zinc-400 hover:text-white hover:bg-zinc-800/50'
              }`
            }
          >
            <item.icon className="w-4 h-4" />
            {item.label}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="px-6 py-4 border-t border-zinc-800">
        <p className="text-xs text-zinc-500">v1.1.0 · 本地服务</p>
      </div>
    </aside>
  )
}
