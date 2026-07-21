import { useEffect, useState } from 'react'

const GROUPS: { heading: string; keys: [string, string][] }[] = [
  {
    heading: 'Triage (You owe lane)',
    keys: [
      ['j / k', 'Move selection down / up'],
      ['e', 'Approve the selected item'],
      ['x', 'Reject the selected item'],
      ['r', 'Reply to the selected item'],
    ],
  },
  {
    heading: 'Global',
    keys: [
      ['⌘ K', 'Command palette'],
      ['?', 'This help'],
      ['Esc', 'Close overlays'],
    ],
  },
]

/** Global keyboard-shortcuts cheat sheet. Opens on `?` (Shift+/) or when the
 * command palette dispatches a `mm:shortcuts` event. Kept intentionally tiny —
 * the real shortcut handling lives with each surface (NowPage, CommandPalette). */
export function ShortcutsHelp() {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = document.activeElement as HTMLElement | null
      const tag = el?.tagName
      const typing = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el?.isContentEditable
      if (!typing && e.key === '?') {
        e.preventDefault()
        setOpen((v) => !v)
      }
      if (e.key === 'Escape') setOpen(false)
    }
    function onOpen() {
      setOpen(true)
    }
    document.addEventListener('keydown', onKey)
    window.addEventListener('mm:shortcuts', onOpen)
    return () => {
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('mm:shortcuts', onOpen)
    }
  }, [])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setOpen(false)}>
      <div
        className="w-full max-w-md overflow-hidden rounded-xl border border-border-strong bg-surface shadow-[var(--shadow-lg)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-border px-4 py-3 text-sm font-semibold">Keyboard shortcuts</div>
        <div className="flex flex-col gap-4 p-4">
          {GROUPS.map((g) => (
            <div key={g.heading}>
              <div className="mb-2 text-[10px] font-bold uppercase tracking-wider text-text-faint">{g.heading}</div>
              <div className="flex flex-col gap-1.5">
                {g.keys.map(([k, label]) => (
                  <div key={k} className="flex items-center justify-between text-[13px]">
                    <span className="text-text-muted">{label}</span>
                    <kbd className="rounded-md border border-border bg-surface-2 px-2 py-0.5 text-[11px] font-semibold text-text">
                      {k}
                    </kbd>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
