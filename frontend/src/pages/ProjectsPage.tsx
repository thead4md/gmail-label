import { useState } from 'react'
import { toast } from 'sonner'
import { Check } from 'lucide-react'
import { PageHeader } from '../components/layout/PageHeader'
import { EmptyState } from '../components/ui/EmptyState'
import { Button } from '../components/ui/Button'
import { useAccount } from '../hooks/useAccount'
import { useProjects, useCloseProject, type Project } from '../hooks/useProjects'
import { formatTs } from '../lib/format'
import { ApiError } from '../lib/api'

export function ProjectsPage() {
  const [account] = useAccount()
  const [status, setStatus] = useState<'active' | 'done' | null>('active')
  const { data, isLoading } = useProjects(account, status)
  const close = useCloseProject()

  async function onClose(project: Project) {
    try {
      await close.mutateAsync(project.id)
      toast.success(`${project.title || 'Project'} closed`)
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Failed to close project')
    }
  }

  if (isLoading) return <div className="p-6 text-text-faint">Loading…</div>

  const items = data?.items ?? []

  return (
    <div>
      <PageHeader title="Projects" subtitle="Threads promoted into durable mini-projects" />
      <div className="mx-auto max-w-3xl px-6 py-5">
        <div className="mb-4 flex gap-2">
          {(['active', 'done', null] as const).map((s) => (
            <button
              key={s ?? 'all'}
              onClick={() => setStatus(s)}
              className={
                'rounded-full px-3 py-1 text-[12px] font-semibold ' +
                (status === s ? 'bg-accent text-white' : 'bg-surface-2 text-text-muted hover:bg-surface-3')
              }
            >
              {s === null ? 'All' : s === 'active' ? 'Active' : 'Done'}
            </button>
          ))}
        </div>

        {items.length === 0 ? (
          <EmptyState
            icon="📁"
            title="No projects yet"
            subtitle="Promote a long thread to a project from the Inbox reading pane."
          />
        ) : (
          <div className="flex flex-col gap-3">
            {items.map((p) => (
              <div key={p.id} className="rounded-xl border border-border bg-surface p-4">
                <div className="mb-2 flex items-start justify-between gap-3">
                  <h3 className="text-[14px] font-bold text-text">{p.title || '(untitled)'}</h3>
                  {p.status === 'active' && (
                    <Button variant="ghost" size="sm" onClick={() => onClose(p)}>
                      <Check size={13} /> Mark done
                    </Button>
                  )}
                </div>
                {p.deadline_ts && (
                  <div className="mb-2 text-[12px] text-danger">⏰ Due {formatTs(p.deadline_ts)}</div>
                )}
                {p.participants.length > 0 && (
                  <div className="mb-2 flex flex-wrap gap-1.5">
                    {p.participants.map((participant) => (
                      <span key={participant.email} className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] text-text-muted">
                        {participant.name || participant.email}
                      </span>
                    ))}
                  </div>
                )}
                {p.action_items.length > 0 && (
                  <ul className="list-disc pl-5 text-[12px] text-text-muted">
                    {p.action_items.map((it, i) => (
                      <li key={i}>{it}</li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
