import { useState } from 'react'
import { toast } from 'sonner'
import { Check, ChevronDown, ChevronRight, Pencil, ShieldCheck, VolumeX, Ban } from 'lucide-react'
import { PageHeader } from '../components/layout/PageHeader'
import { Avatar } from '../components/ui/Avatar'
import { Button } from '../components/ui/Button'
import { EmptyState } from '../components/ui/EmptyState'
import { LabelChip } from '../components/ui/LabelChip'
import { ReasonPanel } from '../components/mail/ReasonPanel'
import { useAccount } from '../hooks/useAccount'
import { useNewSenders, usePendingQueue, useSenderAction } from '../hooks/useReview'
import { useApproveQueueItem, useLabelQueueItem, useRejectQueueItem } from '../hooks/useQueue'
import { useInboxLabels } from '../hooks/useMail'
import { timeAgo, truncate } from '../lib/format'
import type { QueueItem } from '../lib/types'

export function ReviewPage() {
  const [account] = useAccount()
  const { data: newSenders } = useNewSenders(account)
  const senderAction = useSenderAction()
  const [offset, setOffset] = useState(0)
  const { data: pending, isLoading } = usePendingQueue(account, offset, 25)
  const { data: labels } = useInboxLabels(account)

  async function onSenderAction(sender: string, action: 'know' | 'mute' | 'block') {
    await senderAction.mutateAsync({ sender, action })
    toast.success({ know: 'Trusted', mute: 'Muted', block: 'Blocked' }[action])
  }

  return (
    <div>
      <PageHeader title="Review" subtitle="New senders and pending approvals" />
      <div className="mx-auto max-w-3xl px-6 py-5">
        {!!newSenders?.length && (
          <div className="mb-6">
            <SectionHeader icon="🆕" title={`New senders — ${newSenders.length}`} />
            <div className="flex flex-col gap-1.5">
              {newSenders.map((s) => (
                <div key={s.sender} className="flex items-center gap-3 rounded-lg border border-border bg-surface px-3 py-2">
                  <Avatar sender={s.sender} size={28} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px]">{s.sender}</div>
                    <div className="text-[11px] text-text-faint">{s.email_count} emails</div>
                  </div>
                  <Button size="sm" variant="success" onClick={() => onSenderAction(s.sender, 'know')}>
                    <ShieldCheck size={13} /> Know
                  </Button>
                  <Button size="sm" onClick={() => onSenderAction(s.sender, 'mute')}>
                    <VolumeX size={13} /> Mute
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => onSenderAction(s.sender, 'block')}>
                    <Ban size={13} /> Block
                  </Button>
                </div>
              ))}
            </div>
          </div>
        )}

        <SectionHeader icon="⏳" title={`Pending approval — ${pending?.total ?? 0} items`} />
        {isLoading ? (
          <div className="text-xs text-text-faint">Loading…</div>
        ) : !pending?.items.length ? (
          <EmptyState icon="✅" title="Queue is clear" subtitle="All actions have been reviewed" />
        ) : (
          <div className="flex flex-col gap-2">
            {pending.items.map((item) => (
              <ReviewRow key={item.id} item={item} labels={labels ?? []} account={account} />
            ))}
          </div>
        )}
        {pending && pending.total > 25 && (
          <div className="mt-3 flex justify-center gap-2">
            <Button variant="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>
              Previous
            </Button>
            <Button variant="ghost" disabled={offset + 25 >= pending.total} onClick={() => setOffset(offset + 25)}>
              Next
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

function SectionHeader({ icon, title }: { icon: string; title: string }) {
  return (
    <div className="mb-2 flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-text-faint">
      <span>{icon}</span>
      <span>{title}</span>
      <span className="h-px flex-1 bg-border" />
    </div>
  )
}

function ReviewRow({ item, labels, account }: { item: QueueItem; labels: string[]; account: string | null }) {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [newLabel, setNewLabel] = useState(item.primary_label || labels[0] || '')
  const [scope, setScope] = useState<'email' | 'thread' | 'sender'>('email')
  const approve = useApproveQueueItem(account)
  const reject = useRejectQueueItem()
  const labelMut = useLabelQueueItem(account)

  async function onApprove() {
    try {
      await approve.mutateAsync({ id: item.id })
      toast.success('Approved')
    } catch {
      toast.error('Already processed or no longer exists.')
    }
  }
  async function onReject() {
    try {
      await reject.mutateAsync(item.id)
      toast('Rejected')
    } catch {
      toast.error('Already processed or no longer exists.')
    }
  }
  async function onSaveLabel() {
    try {
      await labelMut.mutateAsync({ id: item.id, label: newLabel, scope })
      toast.success(`→ ${newLabel} (${scope})`)
      setEditing(false)
    } catch {
      toast.error('Item no longer exists.')
    }
  }

  return (
    <div className="rounded-xl border border-border bg-surface">
      <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left">
        {open ? <ChevronDown size={14} className="text-text-faint" /> : <ChevronRight size={14} className="text-text-faint" />}
        <span className="truncate text-[13px]">
          <b>{truncate(item.sender, 30)}</b> — {truncate(item.subject, 50)}
        </span>
        <LabelChip label={item.primary_label} />
        <span className="ml-auto shrink-0 text-[11px] text-text-faint">{timeAgo(item.created_at)}</span>
      </button>
      {open && (
        <div className="border-t border-border px-3.5 py-3">
          <div className="mb-2 text-[11px] font-bold uppercase tracking-wider text-text-faint">Why this?</div>
          <ReasonPanel item={item} />
          {item.snippet && <div className="mt-2 rounded-lg bg-surface-2 p-2.5 text-[12px] italic text-text-muted">{item.snippet}</div>}

          <div className="mt-3 flex gap-2">
            <Button variant="success" size="sm" onClick={onApprove} disabled={approve.isPending}>
              <Check size={13} /> Approve
            </Button>
            <Button variant="danger" size="sm" onClick={onReject} disabled={reject.isPending}>
              Reject
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setEditing((v) => !v)}>
              <Pencil size={13} /> Edit label
            </Button>
          </div>

          {editing && (
            <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg bg-surface-2 p-2">
              <select value={newLabel} onChange={(e) => setNewLabel(e.target.value)} className="rounded-md border border-border bg-surface px-2 py-1 text-[12px]">
                {labels.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
              <div className="flex gap-1">
                {(['email', 'thread', 'sender'] as const).map((s) => (
                  <label key={s} className="flex items-center gap-1 text-[11px] text-text-muted">
                    <input type="radio" checked={scope === s} onChange={() => setScope(s)} /> {s}
                  </label>
                ))}
              </div>
              <Button size="sm" variant="primary" onClick={onSaveLabel} disabled={labelMut.isPending}>
                Save
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
