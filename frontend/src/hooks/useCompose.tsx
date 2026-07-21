import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

export interface ComposeTarget {
  mode: 'new' | 'reply'
  gmailId?: string
  threadId?: string | null
  toAddrs?: string
  subject?: string
  /** Open directly into this existing draft (e.g. a Loop Radar nudge already
   * awaiting human review) instead of starting a fresh compose form. */
  draftId?: number
}

interface ComposeCtx {
  target: ComposeTarget | null
  openCompose: (t: ComposeTarget) => void
  closeCompose: () => void
}

const Ctx = createContext<ComposeCtx | null>(null)

export function ComposeProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<ComposeTarget | null>(null)
  const openCompose = useCallback((t: ComposeTarget) => setTarget(t), [])
  const closeCompose = useCallback(() => setTarget(null), [])
  const value = useMemo(() => ({ target, openCompose, closeCompose }), [target, openCompose, closeCompose])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useCompose() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useCompose must be used within ComposeProvider')
  return ctx
}
