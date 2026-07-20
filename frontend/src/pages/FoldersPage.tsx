import { useEffect, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { PageHeader } from '../components/layout/PageHeader'
import { MessageListRow } from '../components/mail/MessageListRow'
import { ReadingPane } from '../components/mail/ReadingPane'
import { EmptyState } from '../components/ui/EmptyState'
import { useAccount } from '../hooks/useAccount'
import { useFolderEmails, useFolders } from '../hooks/useMail'

export function FoldersPage() {
  const [account] = useAccount()
  const { data: labels } = useFolders(account)
  const [label, setLabel] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const { data, isLoading } = useFolderEmails(account, label)

  useEffect(() => {
    if (!label && labels && labels.length > 0) setLabel(labels[0])
  }, [labels, label])

  const items = data?.items ?? []
  const selectedItem = items.find((i) => i.gmail_id === selectedId) ?? null

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Folders"
        subtitle="Browse mail by Gmail label"
        actions={
          <div className="relative">
            <select
              value={label ?? ''}
              onChange={(e) => {
                setLabel(e.target.value)
                setSelectedId(null)
              }}
              className="appearance-none rounded-lg border border-border bg-surface-2 py-1.5 pl-3 pr-8 text-[13px] outline-none focus:border-accent"
            >
              {labels?.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
            <ChevronDown size={13} className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-text-faint" />
          </div>
        }
      />
      <div className="flex min-h-0 flex-1">
        <div className="flex w-[380px] shrink-0 flex-col overflow-y-auto border-r border-border">
          <div className="border-b border-border px-3 py-2 text-[11px] font-semibold text-text-faint">
            {label} — {items.length} email(s)
          </div>
          {isLoading ? null : items.length === 0 ? (
            <EmptyState icon="📭" title="No emails in this folder" subtitle="Nothing local matches this label yet" />
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
              />
            ))
          )}
        </div>
        <div className="min-w-0 flex-1">
          <ReadingPane item={selectedItem} account={account} />
        </div>
      </div>
    </div>
  )
}
