import { useState } from 'react'
import { toast } from 'sonner'
import { ChevronDown, Check, X } from 'lucide-react'
import { PageHeader } from '../components/layout/PageHeader'
import { KpiCard } from '../components/ui/KpiCard'
import { EmptyState } from '../components/ui/EmptyState'
import { SkeletonRow } from '../components/ui/Skeleton'
import { Button } from '../components/ui/Button'
import { MessageCard } from '../components/mail/MessageCard'
import { useAccount } from '../hooks/useAccount'
import { useApproveQueueItem, useDailyBrief, useNow, useRejectQueueItem } from '../hooks/useQueue'
import { truncate } from '../lib/format'
import { ApiError } from '../lib/api'

export function NowPage() {
  const [account] = useAccount()
  const { data, isLoading } = useNow(account)
  const brief = useDailyBrief(account)

  if (isLoading) {
    return (
      <div>
        <PageHeader title="Now" subtitle="High-priority and reply-needed items" />
        <div className="flex flex-col gap-2 p-6">
          <SkeletonRow />
          <SkeletonRow />
          <SkeletonRow />
        </div>
      </div>
    )
  }

  const items = data?.items ?? []

  return (
    <div>
      <PageHeader title="Now" subtitle="High-priority and reply-needed items" />
      <div className="mx-auto max-w-3xl px-6 py-5">
        <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {data?.kpis.map((k) => (
            <KpiCard key={k.label} {...k} />
          ))}
        </div>

        {brief.data?.brief && (
          <details className="mb-5 rounded-xl border border-border bg-surface px-4 py-3">
            <summary className="cursor-pointer text-[13px] font-semibold text-text-muted">📋 Today's brief</summary>
            <div className="mt-2 whitespace-pre-wrap text-[13px] leading-relaxed text-text-muted">{brief.data.brief}</div>
          </details>
        )}

        {items.length === 0 ? (
          <EmptyState icon="✅" title="You're all caught up" subtitle="No high-priority or reply-needed items right now" />
        ) : (
          <>
            <div className="mb-3 text-xs font-semibold text-text-faint">{items.length} need attention</div>
            <div className="flex flex-col gap-2.5">
              {items.map((item) => (
                <NowCard key={item.id} item={item} labels={data?.gmail_labels ?? []} account={account} />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function NowCard({ item, labels, account }: { item: import('../lib/types').QueueItem; labels: string[]; account: string | null }) {
  const [chosenLabel, setChosenLabel] = useState(item.primary_label || labels[0] || '')
  const approve = useApproveQueueItem(account)
  const reject = useRejectQueueItem()
  const reason = item.reason_json || {}

  async function onApprove() {
    try {
      await approve.mutateAsync({
        id: item.id,
        correctedLabel: chosenLabel !== item.primary_label ? chosenLabel : undefined,
      })
      toast.success(truncate(item.subject, 50) || 'Approved')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Already processed or no longer exists.')
    }
  }

  async function onReject() {
    try {
      await reject.mutateAsync(item.id)
      toast(truncate(item.subject, 50) || 'Rejected')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Already processed or no longer exists.')
    }
  }

  return (
    <MessageCard
      subject={item.subject}
      sender={item.sender}
      timestamp={item.created_at}
      label={item.primary_label}
      channel={item.channel}
      confidence={item.confidence}
      snippet={item.snippet}
      replyNeeded={!!reason.reply_needed}
      threadSummary={reason.thread_summary}
      footer={
        <div className="mt-2.5 flex flex-wrap items-center gap-2" onClick={(e) => e.stopPropagation()}>
          {reason.deadlines?.[0] && (
            <span className="rounded-full border border-danger/40 bg-danger-soft px-2 py-0.5 text-[10px] font-bold text-danger">
              ⏰ {reason.deadlines[0]}
            </span>
          )}
          {!!reason.action_items?.length && (
            <details className="text-[11px] text-accent">
              <summary className="cursor-pointer">📋 {reason.action_items.length} action item(s)</summary>
              <ul className="mt-1 list-disc pl-4 text-text-muted">
                {reason.action_items.map((it, i) => (
                  <li key={i}>{it}</li>
                ))}
              </ul>
            </details>
          )}
          <div className="ml-auto flex items-center gap-2">
            <div className="relative">
              <select
                value={chosenLabel}
                onChange={(e) => setChosenLabel(e.target.value)}
                className="appearance-none rounded-lg border border-border bg-surface-2 py-1 pl-2.5 pr-6 text-[12px] outline-none focus:border-accent"
              >
                {labels.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
              <ChevronDown size={12} className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-text-faint" />
            </div>
            <Button variant="success" size="sm" onClick={onApprove} disabled={approve.isPending}>
              <Check size={13} /> Approve
            </Button>
            <Button variant="danger" size="sm" onClick={onReject} disabled={reject.isPending}>
              <X size={13} /> Reject
            </Button>
          </div>
        </div>
      }
    />
  )
}
