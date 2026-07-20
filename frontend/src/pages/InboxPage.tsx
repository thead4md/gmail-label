import { useState } from 'react'
import { toast } from 'sonner'
import { PageHeader } from '../components/layout/PageHeader'
import { MessageListRow } from '../components/mail/MessageListRow'
import { ReadingPane } from '../components/mail/ReadingPane'
import { EmptyState } from '../components/ui/EmptyState'
import { SkeletonRow } from '../components/ui/Skeleton'
import { Button } from '../components/ui/Button'
import { useAccount } from '../hooks/useAccount'
import { useBulkAction, useInbox, useInboxLabels } from '../hooks/useMail'

export function InboxPage() {
  const [account] = useAccount()
  const { data, isLoading } = useInbox(account, 100)
  const { data: labels } = useInboxLabels(account)
  const bulk = useBulkAction(account)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [bulkLabel, setBulkLabel] = useState('')

  const items = data?.items ?? []
  const selectedItem = items.find((i) => i.gmail_id === selectedId) ?? null

  function toggle(id: string) {
    setChecked((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  async function runBulk(action: 'label' | 'archive') {
    const ids = [...checked]
    try {
      const res = await bulk.mutateAsync({ ids, action, label: bulkLabel || undefined })
      const verb = action === 'label' ? 'labeled' : 'archived'
      if (res.failed) {
        toast.warning(`${res.success} of ${ids.length} ${verb}, ${res.failed} failed`)
      } else {
        toast.success(`${res.success} ${verb}`)
      }
      setChecked(new Set())
    } catch {
      toast.error('No Gmail credentials found for this mailbox — cannot execute actions.')
    }
  }

  return (
    <div className="flex h-full flex-col">
      <PageHeader title="Inbox" subtitle={`${items.length} email(s)`} />
      <div className="flex min-h-0 flex-1">
        <div className="flex w-[380px] shrink-0 flex-col border-r border-border">
          {checked.size > 0 && (
            <div className="flex items-center gap-2 border-b border-border bg-surface-2 px-3 py-2">
              <select
                value={bulkLabel}
                onChange={(e) => setBulkLabel(e.target.value)}
                className="rounded-md border border-border bg-surface px-1.5 py-1 text-[11px]"
              >
                <option value="">Label…</option>
                {labels?.map((l) => (
                  <option key={l} value={l}>
                    {l}
                  </option>
                ))}
              </select>
              <Button size="sm" onClick={() => runBulk('label')} disabled={!bulkLabel || bulk.isPending}>
                Apply
              </Button>
              <Button size="sm" variant="danger" onClick={() => runBulk('archive')} disabled={bulk.isPending}>
                Archive
              </Button>
              <span className="ml-auto text-[11px] text-text-faint">{checked.size} selected</span>
            </div>
          )}
          <div className="flex-1 overflow-y-auto">
            {isLoading ? (
              <>
                <SkeletonRow />
                <SkeletonRow />
                <SkeletonRow />
              </>
            ) : items.length === 0 ? (
              <EmptyState icon="📪" title="No mail yet" subtitle="Nothing has been mirrored into the local database yet" />
            ) : (
              items.map((item) => (
                <MessageListRow
                  key={item.gmail_id}
                  subject={item.subject}
                  sender={item.sender}
                  timestamp={item.date_ts}
                  label={item.primary_label}
                  snippet={item.snippet}
                  active={item.gmail_id === selectedId}
                  onClick={() => setSelectedId(item.gmail_id)}
                  checkbox={
                    <input
                      type="checkbox"
                      checked={checked.has(item.gmail_id)}
                      onChange={() => toggle(item.gmail_id)}
                      onClick={(e) => e.stopPropagation()}
                      className="accent-[var(--accent)]"
                    />
                  }
                />
              ))
            )}
          </div>
        </div>
        <div className="min-w-0 flex-1">
          <ReadingPane item={selectedItem} account={account} />
        </div>
      </div>
    </div>
  )
}
