import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { ChevronDown, Check, X, CornerUpLeft, Clock } from 'lucide-react'
import { PageHeader } from '../components/layout/PageHeader'
import { KpiCard } from '../components/ui/KpiCard'
import { EmptyState } from '../components/ui/EmptyState'
import { SkeletonRow } from '../components/ui/Skeleton'
import { Button } from '../components/ui/Button'
import { Avatar } from '../components/ui/Avatar'
import { MessageCard } from '../components/mail/MessageCard'
import { useAccount } from '../hooks/useAccount'
import { useApproveQueueItem, useDailyBrief, useNow, useRejectQueueItem } from '../hooks/useQueue'
import { useCompose } from '../hooks/useCompose'
import { truncate, timeAgo, extractDisplayName } from '../lib/format'
import { ApiError } from '../lib/api'
import type { Loop, QueueItem } from '../lib/types'

export function NowPage() {
  const [account] = useAccount()
  const { data, isLoading } = useNow(account)
  const brief = useDailyBrief(account)
  const { openCompose } = useCompose()

  const approve = useApproveQueueItem(account)
  const reject = useRejectQueueItem()

  const youOwe = data?.you_owe ?? data?.items ?? []
  const waitingOn = data?.waiting_on ?? []
  const handled = data?.handled ?? []
  const counts = data?.counts

  // ── Keyboard-first triage over the "You owe" lane ──────────────────────
  const [sel, setSel] = useState(0)
  const listRef = useRef<HTMLDivElement>(null)
  const safeSel = Math.min(sel, Math.max(0, youOwe.length - 1))

  async function doApprove(item: QueueItem, correctedLabel?: string) {
    try {
      await approve.mutateAsync({ id: item.id, correctedLabel })
      toast.success(truncate(item.subject ?? '', 50) || 'Approved')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Already processed or no longer exists.')
    }
  }
  async function doReject(item: QueueItem) {
    try {
      await reject.mutateAsync(item.id)
      toast(truncate(item.subject ?? '', 50) || 'Rejected')
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : 'Already processed or no longer exists.')
    }
  }
  function doReply(item: QueueItem) {
    openCompose({ mode: 'reply', gmailId: item.email_gmail_id })
  }
  function doNudge(loop: Loop) {
    if (loop.state === 'nudge_drafted' && loop.draft_id) {
      // Loop Radar already drafted a nudge for this contact and it's
      // awaiting human review — open that exact draft (Approve → Send),
      // never create a second one alongside it.
      openCompose({ mode: 'new', draftId: loop.draft_id })
      return
    }
    // A waiting-on loop has no inbound message to "reply" to, so open a fresh
    // message prefilled with the contact + Re: subject as a follow-up nudge.
    openCompose({
      mode: 'new',
      toAddrs: loop.contact_email ?? '',
      subject: loop.subject ? `Re: ${loop.subject}` : '',
    })
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = document.activeElement as HTMLElement | null
      const tag = el?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el?.isContentEditable) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (youOwe.length === 0) return
      const cur = Math.min(sel, youOwe.length - 1)
      switch (e.key) {
        case 'j':
          setSel((s) => Math.min(youOwe.length - 1, s + 1))
          e.preventDefault()
          break
        case 'k':
          setSel((s) => Math.max(0, s - 1))
          e.preventDefault()
          break
        case 'e':
          void doApprove(youOwe[cur])
          e.preventDefault()
          break
        case 'x':
          void doReject(youOwe[cur])
          e.preventDefault()
          break
        case 'r':
          doReply(youOwe[cur])
          e.preventDefault()
          break
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [youOwe, sel])

  useEffect(() => {
    const node = listRef.current?.querySelector(`[data-loop-idx="${safeSel}"]`)
    node?.scrollIntoView({ block: 'nearest' })
  }, [safeSel, youOwe.length])

  if (isLoading) {
    return (
      <div>
        <PageHeader title="Loops" subtitle="What you owe, and who owes you" />
        <div className="flex flex-col gap-2 p-6">
          <SkeletonRow />
          <SkeletonRow />
          <SkeletonRow />
        </div>
      </div>
    )
  }

  const allClear = youOwe.length === 0 && waitingOn.length === 0

  return (
    <div>
      <PageHeader title="Loops" subtitle="What you owe, and who owes you" />
      <div className="mx-auto max-w-3xl px-6 py-5">
        {/* The aha line — the whole inbox in one sentence. */}
        {counts && (
          <div className="mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-[15px] font-semibold">
            <span className="text-text">You owe {counts.you_owe}</span>
            <span className="text-text-faint">·</span>
            <span className="text-text">{counts.waiting_on} waiting on you</span>
            {counts.slipping > 0 && (
              <>
                <span className="text-text-faint">·</span>
                <span className="text-danger">{counts.slipping} about to slip</span>
              </>
            )}
            <span className="ml-auto text-[11px] font-normal text-text-faint">
              press <kbd className="rounded bg-surface-2 px-1">?</kbd> for shortcuts
            </span>
          </div>
        )}

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

        {allClear ? (
          <EmptyState icon="✅" title="All loops closed" subtitle="Nothing you owe, nobody kept waiting. Enjoy it." />
        ) : (
          <div className="flex flex-col gap-7">
            {/* ── Lane 1: You owe ─────────────────────────────────────── */}
            <section>
              <LaneHeader icon="✍️" title="You owe" count={youOwe.length} hint="a reply or a decision" />
              {youOwe.length === 0 ? (
                <LaneEmpty text="Nothing needs a reply or decision from you right now." />
              ) : (
                <div ref={listRef} className="flex flex-col gap-2.5">
                  {youOwe.map((item, i) => (
                    <YouOweCard
                      key={item.id}
                      item={item}
                      idx={i}
                      selected={i === safeSel}
                      labels={data?.gmail_labels ?? []}
                      onSelect={() => setSel(i)}
                      onApprove={(label) => doApprove(item, label)}
                      onReject={() => doReject(item)}
                      onReply={() => doReply(item)}
                      busy={approve.isPending || reject.isPending}
                    />
                  ))}
                </div>
              )}
            </section>

            {/* ── Lane 2: Waiting on ──────────────────────────────────── */}
            <section>
              <LaneHeader icon="⏳" title="Waiting on" count={waitingOn.length} hint="someone owes you a reply" />
              {waitingOn.length === 0 ? (
                <LaneEmpty text="No open threads where you're waiting on a reply." />
              ) : (
                <div className="flex flex-col gap-2">
                  {waitingOn.map((loop) => (
                    <WaitingCard key={loop.id} loop={loop} onNudge={() => doNudge(loop)} />
                  ))}
                </div>
              )}
            </section>

            {/* ── Lane 3: Handled (collapsed) ─────────────────────────── */}
            {handled.length > 0 && (
              <details className="rounded-xl border border-border bg-surface px-4 py-3">
                <summary className="cursor-pointer text-[13px] font-semibold text-text-muted">
                  ✅ Handled ({handled.length})
                </summary>
                <div className="mt-3 flex flex-col divide-y divide-border">
                  {handled.map((h) => (
                    <div key={h.id} className="flex items-center gap-3 py-2 text-[12px]">
                      <span className="shrink-0 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] font-semibold text-text-muted">
                        {h.was_auto ? '🤖 auto' : '✔ approved'}
                      </span>
                      <span className="truncate font-medium text-text">{extractDisplayName(h.sender) || 'Unknown'}</span>
                      <span className="truncate text-text-muted">{h.subject || '(no subject)'}</span>
                      {h.primary_label && (
                        <span className="ml-auto shrink-0 text-[10px] text-text-faint">{h.primary_label}</span>
                      )}
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function LaneHeader({ icon, title, count, hint }: { icon: string; title: string; count: number; hint: string }) {
  return (
    <div className="mb-2.5 flex items-baseline gap-2">
      <span className="text-sm">{icon}</span>
      <h2 className="text-[13px] font-bold uppercase tracking-wider text-text">{title}</h2>
      <span className="rounded-full bg-surface-2 px-1.5 text-[11px] font-semibold tabular-nums text-text-muted">{count}</span>
      <span className="text-[11px] text-text-faint">— {hint}</span>
    </div>
  )
}

function LaneEmpty({ text }: { text: string }) {
  return <div className="rounded-xl border border-dashed border-border px-4 py-4 text-[12px] text-text-faint">{text}</div>
}

function YouOweCard({
  item,
  idx,
  selected,
  labels,
  onSelect,
  onApprove,
  onReject,
  onReply,
  busy,
}: {
  item: QueueItem
  idx: number
  selected: boolean
  labels: string[]
  onSelect: () => void
  onApprove: (correctedLabel?: string) => void
  onReject: () => void
  onReply: () => void
  busy: boolean
}) {
  const [chosenLabel, setChosenLabel] = useState(item.primary_label || labels[0] || '')
  const reason = item.reason_json || {}

  return (
    <div
      data-loop-idx={idx}
      onClick={onSelect}
      className={selected ? 'rounded-xl ring-2 ring-accent ring-offset-2 ring-offset-bg' : ''}
    >
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
              {!!reason.reply_needed && (
                <Button variant="ghost" size="sm" onClick={onReply}>
                  <CornerUpLeft size={13} /> Reply
                </Button>
              )}
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
              <Button
                variant="success"
                size="sm"
                onClick={() => onApprove(chosenLabel !== item.primary_label ? chosenLabel : undefined)}
                disabled={busy}
              >
                <Check size={13} /> Approve
              </Button>
              <Button variant="danger" size="sm" onClick={onReject} disabled={busy}>
                <X size={13} /> Reject
              </Button>
            </div>
          </div>
        }
      />
    </div>
  )
}

function WaitingCard({ loop, onNudge }: { loop: Loop; onNudge: () => void }) {
  const name = loop.contact_name || loop.contact_email || 'someone'
  const days = loop.waiting_days ?? 0
  const state = loop.state || 'open'

  let stateBadge: React.ReactNode = null
  if (state === 'escalated') {
    stateBadge = (
      <span className="shrink-0 rounded-full border border-danger/40 bg-danger-soft px-2 py-0.5 text-[10px] font-bold text-danger">
        🚨 no reply after {loop.nudge_count} nudge{loop.nudge_count === 1 ? '' : 's'}
      </span>
    )
  } else if (state === 'nudge_drafted') {
    stateBadge = (
      <span className="shrink-0 rounded-full border border-accent/40 bg-accent-soft px-2 py-0.5 text-[10px] font-bold text-accent">
        ✨ nudge drafted — review to send
      </span>
    )
  } else if (state === 'nudged') {
    stateBadge = (
      <span className="shrink-0 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] font-semibold text-text-muted">
        nudged {loop.nudge_count}×{loop.last_nudge_ts ? ` · ${timeAgo(loop.last_nudge_ts)}` : ''}
      </span>
    )
  } else if (loop.slipping) {
    stateBadge = (
      <span className="shrink-0 rounded-full border border-danger/40 bg-danger-soft px-2 py-0.5 text-[10px] font-bold text-danger">
        about to slip
      </span>
    )
  }

  return (
    <div className="flex items-center gap-3 rounded-xl border border-border bg-surface px-4 py-3">
      <Avatar sender={loop.contact_name || loop.contact_email} size={32} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-[13px] font-semibold text-text">{name}</span>
          {stateBadge}
        </div>
        <div className="truncate text-[12px] text-text-muted">{loop.subject || '(no subject)'}</div>
      </div>
      <div className="flex shrink-0 items-center gap-1 text-[11px] text-text-faint">
        <Clock size={12} /> {days === 0 ? 'today' : `${days}d`}
        {loop.last_sent_ts ? <span className="hidden sm:inline">· sent {timeAgo(loop.last_sent_ts)}</span> : null}
      </div>
      <Button variant="ghost" size="sm" onClick={onNudge}>
        <CornerUpLeft size={13} /> {state === 'nudge_drafted' ? 'Review' : 'Nudge'}
      </Button>
    </div>
  )
}
