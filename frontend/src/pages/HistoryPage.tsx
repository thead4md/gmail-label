import { useState } from 'react'
import { toast } from 'sonner'
import { ChevronDown, ChevronRight, Pencil } from 'lucide-react'
import { PageHeader } from '../components/layout/PageHeader'
import { Button } from '../components/ui/Button'
import { EmptyState } from '../components/ui/EmptyState'
import { LabelChip } from '../components/ui/LabelChip'
import { ReasonPanel } from '../components/mail/ReasonPanel'
import { useAccount } from '../hooks/useAccount'
import { useAuditLog, useCorrections, useExecutedHistory } from '../hooks/useHistory'
import { useCorrectQueueItem } from '../hooks/useQueue'
import { useInboxLabels } from '../hooks/useMail'
import { formatTs, timeAgo, truncate } from '../lib/format'
import type { QueueItem } from '../lib/types'

const STATUS_ICON: Record<string, string> = { executed: '✅', approved: '👍', execute_failed: '⚠️' }

export function HistoryPage() {
  const [account] = useAccount()
  const [days, setDays] = useState(7)
  const { data, isLoading } = useExecutedHistory(account, days)
  const { data: corrections } = useCorrections()
  const { data: labels } = useInboxLabels(account)
  const { data: audit } = useAuditLog(account, days)

  return (
    <div>
      <PageHeader title="History" subtitle="Recent activity and corrections" />
      <div className="mx-auto max-w-3xl px-6 py-5">
        <div className="mb-4 flex items-center gap-3">
          <span className="text-[11px] font-bold uppercase tracking-wider text-text-faint">Window</span>
          <input type="range" min={1} max={30} value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-40 accent-[var(--accent)]" />
          <span className="text-[12px] tabular-nums text-text-muted">{days} days</span>
        </div>

        {isLoading ? null : !data?.items.length ? (
          <EmptyState icon="📭" title="No activity in this window" subtitle="Widen the slider or run the pipeline" />
        ) : (
          <div className="mb-8 flex flex-col gap-2">
            {data.items.map((item) => (
              <HistoryRow key={item.id} item={item} labels={labels ?? []} account={account} />
            ))}
          </div>
        )}

        <div className="mb-2 flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-text-faint">
          <span>🔍</span>
          <span>Audit trail</span>
          <span className="h-px flex-1 bg-border" />
        </div>
        {!audit?.items.length ? (
          <div className="mb-8 text-xs text-text-faint">No autonomous or sent actions in this window.</div>
        ) : (
          <div className="mb-8 flex flex-col divide-y divide-border overflow-hidden rounded-xl border border-border">
            {audit.items.map((a) => (
              <div key={`${a.kind}-${a.ref_id}`} className="flex items-center gap-2 px-3.5 py-2 text-[12px]">
                <span className="shrink-0">{a.kind === 'label' ? '🏷️' : a.kind === 'sent' ? '📧' : '📅'}</span>
                <span className="min-w-0 flex-1 truncate text-text">{a.summary || '(no subject)'}</span>
                <span className="shrink-0 truncate text-text-faint">{a.detail}</span>
                {!!a.was_auto && (
                  <span className="shrink-0 rounded-full bg-warning-soft px-2 py-0.5 text-[10px] font-bold text-warning">AUTO</span>
                )}
                <span className="shrink-0 text-[11px] text-text-faint">{timeAgo(a.when_ts)}</span>
              </div>
            ))}
          </div>
        )}

        <div className="mb-2 flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-text-faint">
          <span>✏️</span>
          <span>Correction history</span>
          <span className="h-px flex-1 bg-border" />
        </div>
        {!corrections?.length ? (
          <div className="text-xs text-text-faint">No corrections yet — correct a label above to start training the system.</div>
        ) : (
          <div className="overflow-hidden rounded-xl border border-border">
            <table className="w-full text-[12px]">
              <thead className="bg-surface-2 text-[10px] uppercase tracking-wider text-text-faint">
                <tr>
                  <th className="px-3 py-2 text-left">Date</th>
                  <th className="px-3 py-2 text-left">Original</th>
                  <th className="px-3 py-2 text-left"></th>
                  <th className="px-3 py-2 text-left">Corrected</th>
                  <th className="px-3 py-2 text-left">Source</th>
                </tr>
              </thead>
              <tbody>
                {corrections.map((c, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className="whitespace-nowrap px-3 py-2 text-text-muted">{formatTs(c.created_at)}</td>
                    <td className="px-3 py-2">
                      <LabelChip label={c.original_label} />
                    </td>
                    <td className="px-3 py-2 text-text-faint">→</td>
                    <td className="px-3 py-2">
                      <LabelChip label={c.corrected_label} />
                    </td>
                    <td className="px-3 py-2 text-text-muted">{c.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

function HistoryRow({ item, labels, account }: { item: QueueItem; labels: string[]; account: string | null }) {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [newLabel, setNewLabel] = useState(item.primary_label || labels[0] || '')
  const correct = useCorrectQueueItem(account)

  async function onSave() {
    try {
      await correct.mutateAsync({ id: item.id, label: newLabel })
      toast.success(`Corrected → ${newLabel}`)
      setEditing(false)
    } catch {
      toast.error('Item no longer found in queue.')
    }
  }

  const actionedTs = item.executed_at || item.reviewed_at || item.created_at

  return (
    <div className="rounded-xl border border-border bg-surface">
      <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left">
        {open ? <ChevronDown size={14} className="text-text-faint" /> : <ChevronRight size={14} className="text-text-faint" />}
        <span>{STATUS_ICON[item.status] ?? '•'}</span>
        <span className="truncate text-[13px]">
          <b>{truncate(item.sender, 30)}</b> — {truncate(item.subject, 50)}
        </span>
        <LabelChip label={item.primary_label} />
        {item.was_auto && <span className="rounded-full bg-warning-soft px-2 py-0.5 text-[10px] font-bold text-warning">AUTO</span>}
        <span className="ml-auto shrink-0 text-[11px] text-text-faint">{timeAgo(actionedTs)}</span>
      </button>
      {open && (
        <div className="border-t border-border px-3.5 py-3">
          <div className="mb-2 text-[11px] font-bold uppercase tracking-wider text-text-faint">Why this?</div>
          <ReasonPanel item={item} />
          {item.snippet && <div className="mt-2 rounded-lg bg-surface-2 p-2.5 text-[12px] italic text-text-muted">{item.snippet}</div>}
          <div className="mt-3">
            <Button variant="ghost" size="sm" onClick={() => setEditing((v) => !v)}>
              <Pencil size={13} /> Correct label
            </Button>
          </div>
          {editing && (
            <div className="mt-2 flex items-center gap-2 rounded-lg bg-surface-2 p-2">
              <select value={newLabel} onChange={(e) => setNewLabel(e.target.value)} className="rounded-md border border-border bg-surface px-2 py-1 text-[12px]">
                {labels.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
              <Button size="sm" variant="primary" onClick={onSave} disabled={correct.isPending}>
                Save
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
