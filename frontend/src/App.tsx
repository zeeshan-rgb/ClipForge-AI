import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { api } from './api'
import { useLiveUpdates } from './live'
import Analysis from './pages/Analysis'
import ClipEditor from './pages/ClipEditor'
import Dashboard from './pages/Dashboard'
import NewProject from './pages/NewProject'
import Permissions from './pages/Permissions'
import ProjectDetail from './pages/ProjectDetail'
import Settings from './pages/Settings'
import Shorts from './pages/Shorts'

const icon = (d: string) => (
  <svg viewBox="0 0 20 20" className="size-[18px] shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d={d} strokeLinecap="round" strokeLinejoin="round" />
  </svg>
)

const NAV: { to: string; label: string; icon: ReactNode; end?: boolean }[] = [
  { to: '/', label: 'Dashboard', end: true, icon: icon('M3.5 10.5l6.5-6 6.5 6M5.5 9v7h9V9') },
  { to: '/new', label: 'New project', icon: icon('M10 4.5v11M4.5 10h11') },
  { to: '/analysis', label: 'AI analysis', icon: icon('M3.5 15.5l4-5 3 3 6-8M3.5 3.5v13h13') },
  { to: '/editor', label: 'Clip editor', icon: icon('M4 5.5h12M4 10h12M4 14.5h7M14.5 13v4M12.5 15h4') },
  { to: '/shorts', label: 'Generated Shorts', icon: icon('M6.5 3.5h7a1 1 0 011 1v11a1 1 0 01-1 1h-7a1 1 0 01-1-1v-11a1 1 0 011-1zM8.8 8v4l3.2-2z') },
  { to: '/permissions', label: 'Permissions', icon: icon('M3.5 6l6.5 4.5L16.5 6M3.5 5.5h13v9h-13zM12.5 13l1.5 1.5 3-3') },
  { to: '/settings', label: 'Settings', icon: icon('M10 12.5a2.5 2.5 0 100-5 2.5 2.5 0 000 5zM10 3v2M10 15v2M3 10h2M15 10h2M5 5l1.4 1.4M13.6 13.6L15 15M5 15l1.4-1.4M13.6 6.4L15 5') },
]

export default function App() {
  const connected = useLiveUpdates()
  const system = useQuery({ queryKey: ['system'], queryFn: api.system, staleTime: 60_000 })

  return (
    <div className="flex h-full">
      <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-panel">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <span className="grid size-8 place-items-center rounded-lg bg-accent text-accent-ink">
            <svg viewBox="0 0 16 16" className="size-4" fill="currentColor">
              <path d="M5 3v10l8-5z" />
            </svg>
          </span>
          <span className="text-[15px] font-semibold tracking-tight">ClipForge AI</span>
        </div>

        <nav className="flex-1 space-y-0.5 px-3" aria-label="Main">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                  isActive ? 'bg-raised font-medium text-ink' : 'text-soft hover:bg-raised/60 hover:text-ink'
                }`
              }
            >
              {item.icon}
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-line px-5 py-4 text-xs text-faint">
          <div className="flex items-center gap-2">
            <span className={`size-2 rounded-full ${connected ? 'bg-good' : 'bg-bad'}`} />
            {connected ? 'Connected' : 'Reconnecting…'}
          </div>
          {system.data && <div className="mt-1.5">Version {system.data.version}</div>}
        </div>
      </aside>

      <main className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-6xl px-8 py-8">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/new" element={<NewProject />} />
            <Route path="/analysis" element={<Analysis />} />
            <Route path="/analysis/:id" element={<ProjectDetail />} />
            <Route path="/editor" element={<ClipEditor />} />
            <Route path="/shorts" element={<Shorts />} />
            <Route path="/permissions" element={<Permissions />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Dashboard />} />
          </Routes>
        </div>
      </main>
    </div>
  )
}
