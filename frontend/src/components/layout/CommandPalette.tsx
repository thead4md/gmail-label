import { Command } from 'cmdk'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Sparkles,
  ClipboardCheck,
  History,
  Inbox,
  Search,
  FolderOpen,
  BarChart3,
  Settings,
  PenSquare,
} from 'lucide-react'

const PAGES = [
  { to: '/now', label: 'Go to Now', icon: Sparkles },
  { to: '/review', label: 'Go to Review', icon: ClipboardCheck },
  { to: '/inbox', label: 'Go to Inbox', icon: Inbox },
  { to: '/search', label: 'Go to Search', icon: Search },
  { to: '/folders', label: 'Go to Folders', icon: FolderOpen },
  { to: '/history', label: 'Go to History', icon: History },
  { to: '/insights', label: 'Go to Insights', icon: BarChart3 },
  { to: '/automate', label: 'Go to Automate', icon: Settings },
]

export function CommandPalette({ onCompose }: { onCompose: () => void }) {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((v) => !v)
      }
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 pt-[15vh]"
      onClick={() => setOpen(false)}
    >
      <Command
        className="w-full max-w-lg overflow-hidden rounded-xl border border-border-strong bg-surface shadow-[var(--shadow-lg)]"
        onClick={(e) => e.stopPropagation()}
        loop
      >
        <Command.Input
          autoFocus
          placeholder="Jump to… or search commands"
          className="w-full border-b border-border bg-transparent px-4 py-3 text-sm outline-none placeholder:text-text-faint"
        />
        <Command.List className="max-h-80 overflow-y-auto p-2">
          <Command.Empty className="px-3 py-6 text-center text-xs text-text-faint">No matches.</Command.Empty>
          <Command.Group heading="Actions" className="px-1 pb-1 text-[10px] font-bold uppercase tracking-wider text-text-faint [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
            <Command.Item
              onSelect={() => {
                onCompose()
                setOpen(false)
              }}
              className="flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] text-text data-[selected=true]:bg-accent-soft data-[selected=true]:text-accent"
            >
              <PenSquare size={15} /> Compose new message
            </Command.Item>
          </Command.Group>
          <Command.Group heading="Navigate" className="px-1 text-[10px] font-bold uppercase tracking-wider text-text-faint [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
            {PAGES.map((p) => (
              <Command.Item
                key={p.to}
                onSelect={() => {
                  navigate(p.to)
                  setOpen(false)
                }}
                className="flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] text-text data-[selected=true]:bg-accent-soft data-[selected=true]:text-accent"
              >
                <p.icon size={15} /> {p.label}
              </Command.Item>
            ))}
          </Command.Group>
        </Command.List>
      </Command>
    </div>
  )
}
