import type { ReactNode } from 'react'
import { labelColor } from '../../lib/taxonomy'
import { extractDisplayName, timeAgo, truncate } from '../../lib/format'
import { Avatar } from '../ui/Avatar'
import { LabelChip } from '../ui/LabelChip'
import { ChannelChip } from '../ui/ChannelChip'
import { ConfidenceBar } from '../ui/ConfidenceBar'

interface Props {
  subject?: string | null
  sender?: string | null
  timestamp?: number | null
  label?: string | null
  channel?: string | null
  confidence?: number | null
  snippet?: string | null
  replyNeeded?: boolean
  threadSummary?: string | null
  checkbox?: ReactNode
  onClick?: () => void
  selected?: boolean
  footer?: ReactNode
}

export function MessageCard({
  subject,
  sender,
  timestamp,
  label,
  channel,
  confidence,
  snippet,
  replyNeeded,
  threadSummary,
  checkbox,
  onClick,
  selected,
  footer,
}: Props) {
  const color = labelColor(label)
  return (
    <div
      onClick={onClick}
      className={
        'group flex gap-3 rounded-xl border px-3.5 py-3 transition-colors ' +
        (selected ? 'border-accent-border bg-accent-soft' : 'border-border bg-surface hover:bg-surface-hover') +
        (onClick ? ' cursor-pointer' : '')
      }
      style={{ borderLeftColor: color, borderLeftWidth: 3 }}
    >
      {checkbox}
      <Avatar sender={sender} />
      <div className="min-w-0 flex-1">
        <div className="mb-0.5 flex items-baseline justify-between gap-2">
          <span className="truncate text-[12px] font-semibold text-text-muted">{extractDisplayName(sender)}</span>
          <span className="shrink-0 text-[11px] text-text-faint">{timeAgo(timestamp)}</span>
        </div>
        <div className="mb-1 truncate text-[14px] font-semibold text-text">{truncate(subject, 90) || '[No Subject]'}</div>
        {threadSummary && (
          <div className="mb-1 truncate text-[12px] italic text-text-muted">&ldquo;{truncate(threadSummary, 120)}&rdquo;</div>
        )}
        <div className="flex flex-wrap items-center gap-1.5">
          <LabelChip label={label} />
          <ChannelChip channel={channel} />
          <ConfidenceBar value={confidence} />
          {replyNeeded && (
            <span className="inline-flex items-center gap-1 rounded-full border border-accent-border bg-accent-soft px-2 py-0.5 text-[10px] font-bold text-accent">
              💬 Reply needed
            </span>
          )}
        </div>
        {snippet && <div className="mt-1.5 truncate text-[12px] text-text-faint">{snippet}</div>}
        {footer}
      </div>
    </div>
  )
}
