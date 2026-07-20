import { NavLink } from 'react-router-dom'
import clsx from 'clsx'
import {
  Sparkles,
  ClipboardCheck,
  History,
  Inbox,
  Search,
  FolderOpen,
  BarChart3,
  Settings,
  Moon,
  Sun,
  Monitor,
  PenSquare,
} from 'lucide-react'
import { useAccount, useMeta } from '../../hooks/useAccount'
import { useTheme } from '../../hooks/useTheme'

const NAV = [
  { to: '/now', label: 'Now', icon: Sparkles },
  { to: '/review', label: 'Review', icon: ClipboardCheck },
  { to: '/inbox', label: 'Inbox', icon: Inbox },
  { to: '/search', label: 'Search', icon: Search },
  { to: '/folders', label: 'Folders', icon: FolderOpen },
  { to: '/history', label: 'History', icon: History },
  { to: '/insights', label: 'Insights', icon: BarChart3 },
  { to: '/automate', label: 'Automate', icon: Settings },
]

const THEME_OPTIONS = [
  { value: 'dark', icon: Moon, label: 'Dark' },
  { value: 'light', icon: Sun, label: 'Light' },
  { value: 'system', icon: Monitor, label: 'System' },
] as const

export function Sidebar({ onCompose }: { onCompose: () => void }) {
  const [account, setAccount, accounts] = useAccount()
  const { data: meta } = useMeta()
  const [theme, setTheme] = useTheme()
  const hb = meta?.heartbeat

  return (
    <aside className="flex h-full w-60 shrink-0 flex-col border-r border-border bg-surface px-3 py-4">
      <div className="mb-4 flex items-center gap-2 px-2">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-accent text-sm font-bold text-white">
          M
        </div>
        <div>
          <div className="text-[14px] font-bold leading-tight">MailMind</div>
          <div className="text-[10px] leading-tight text-text-faint">AI email assistant</div>
        </div>
      </div>

      <button
        onClick={onCompose}
        className="mb-4 flex items-center justify-center gap-2 rounded-lg bg-accent px-3 py-2 text-[13px] font-semibold text-white shadow-[var(--shadow-sm)] transition-colors hover:bg-accent-hover"
      >
        <PenSquare size={15} /> Compose
      </button>

      <nav className="flex flex-col gap-0.5">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              clsx(
                'flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] font-medium transition-colors',
                isActive
                  ? 'bg-accent-soft text-accent'
                  : 'text-text-muted hover:bg-surface-2 hover:text-text',
              )
            }
          >
            <item.icon size={16} strokeWidth={2} />
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="flex-1" />

      {accounts.length > 1 && (
        <div className="mb-3 border-t border-border pt-3">
          <div className="mb-1.5 px-2 text-[10px] font-bold uppercase tracking-wider text-text-faint">Mailbox</div>
          <select
            value={account ?? ''}
            onChange={(e) => setAccount(e.target.value)}
            className="w-full rounded-lg border border-border bg-surface-2 px-2 py-1.5 text-[12px] text-text outline-none focus:border-accent"
          >
            {accounts.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </div>
      )}

      {hb && (
        <div className="mb-3 flex items-center gap-1.5 border-t border-border px-2 pt-3 text-[11px] text-text-muted">
          <span
            className={clsx('h-1.5 w-1.5 rounded-full', {
              'bg-success shadow-[0_0_6px_var(--success)]': hb.status === 'fresh',
              'bg-danger': hb.status === 'stale',
              'bg-warning': hb.status === 'never',
            })}
          />
          <span className="truncate">Watcher: {hb.human}</span>
        </div>
      )}

      <div className="flex gap-1 rounded-lg border border-border bg-surface-2 p-1">
        {THEME_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            onClick={() => setTheme(opt.value)}
            title={opt.label}
            className={clsx(
              'flex flex-1 items-center justify-center rounded-md py-1 transition-colors',
              theme === opt.value ? 'bg-surface-3 text-accent' : 'text-text-faint hover:text-text-muted',
            )}
          >
            <opt.icon size={13} />
          </button>
        ))}
      </div>
    </aside>
  )
}
