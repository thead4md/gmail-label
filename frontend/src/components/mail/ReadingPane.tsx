import { Reply } from 'lucide-react'
import type { EmailListItem } from '../../lib/types'
import { extractDisplayName, formatTs, timeAgo } from '../../lib/format'
import { Avatar } from '../ui/Avatar'
import { LabelChip } from '../ui/LabelChip'
import { ChannelChip } from '../ui/ChannelChip'
import { ConfidenceBar } from '../ui/ConfidenceBar'
import { Button } from '../ui/Button'
import { EmptyState } from '../ui/EmptyState'
import { useThread } from '../../hooks/useMail'
import { useCompose } from '../../hooks/useCompose'

export function ReadingPane({ item, account }: { item: EmailListItem | null; account: string | null }) {
  const thread = useThread(account, item?.thread_id)
  const { openCompose } = useCompose()

  if (!item) {
    return (
      <div className="flex h-full items-center justify-center">
        <EmptyState icon="👀" title="Select a message" subtitle="Pick something from the list to read it here" />
      </div>
    )
  }

  const messages = (thread.data && thread.data.length > 1 ? thread.data : [item]).slice()

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="border-b border-border px-6 py-4">
        <div className="mb-2 flex items-center gap-2">
          <LabelChip label={item.primary_label} />
          <ChannelChip channel={item.channel} />
          <ConfidenceBar value={item.confidence} />
        </div>
        <h2 className="text-[16px] font-bold leading-snug">{item.subject || '[No Subject]'}</h2>
      </div>

      <div className="flex-1 px-6 py-4">
        {messages.map((m, i) => (
          <div key={m.gmail_id + i} className="mb-4 rounded-xl border border-border bg-surface p-4">
            <div className="mb-3 flex items-center gap-3">
              <Avatar sender={m.sender} size={32} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-semibold">{extractDisplayName(m.sender)}</div>
                <div className="text-[11px] text-text-faint">{timeAgo(m.date_ts)} · {formatTs(m.date_ts)}</div>
              </div>
            </div>
            <div className="whitespace-pre-wrap text-[13px] leading-relaxed text-text-muted">{m.snippet}</div>
          </div>
        ))}

        <Button
          variant="default"
          onClick={() =>
            openCompose({ mode: 'reply', gmailId: item.gmail_id, threadId: item.thread_id, toAddrs: item.sender ?? undefined, subject: item.subject ?? undefined })
          }
        >
          <Reply size={14} /> Reply
        </Button>
      </div>
    </div>
  )
}
