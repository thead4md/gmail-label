import { useEffect, useState } from 'react'
import { Search as SearchIcon } from 'lucide-react'
import { PageHeader } from '../components/layout/PageHeader'
import { MessageListRow } from '../components/mail/MessageListRow'
import { ReadingPane } from '../components/mail/ReadingPane'
import { EmptyState } from '../components/ui/EmptyState'
import { useAccount } from '../hooks/useAccount'
import { useSearch } from '../hooks/useMail'

export function SearchPage() {
  const [account] = useAccount()
  const [input, setInput] = useState('')
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const { data, isFetching } = useSearch(account, query)

  // Live search, debounced — no explicit submit step needed (also sidesteps
  // relying on the browser's implicit "Enter submits the sole input" form
  // behavior, which not every input method triggers reliably).
  useEffect(() => {
    const id = setTimeout(() => setQuery(input), 250)
    return () => clearTimeout(id)
  }, [input])

  const items = data?.items ?? []
  const selectedItem = items.find((i) => i.gmail_id === selectedId) ?? null

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Search"
        subtitle="Search subject, sender, and body text"
        actions={
          <div className="flex items-center gap-2 rounded-lg border border-border bg-surface-2 px-3 py-1.5">
            <SearchIcon size={14} className="text-text-faint" />
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Search mail…"
              className="w-64 bg-transparent text-[13px] outline-none placeholder:text-text-faint"
            />
          </div>
        }
      />
      <div className="flex min-h-0 flex-1">
        <div className="flex w-[380px] shrink-0 flex-col overflow-y-auto border-r border-border">
          {!query ? (
            <EmptyState icon="🔍" title="Type something to search" subtitle="Searches subject, sender, and body text" />
          ) : isFetching ? (
            <div className="p-6 text-center text-xs text-text-faint">Searching…</div>
          ) : items.length === 0 ? (
            <EmptyState icon="📭" title="No results" subtitle={`Nothing matched "${query}"`} />
          ) : (
            <>
              <div className="border-b border-border px-3 py-2 text-[11px] font-semibold text-text-faint">
                {items.length} result(s)
              </div>
              {items.map((item) => (
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
              ))}
            </>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <ReadingPane item={selectedItem} account={account} />
        </div>
      </div>
    </div>
  )
}
