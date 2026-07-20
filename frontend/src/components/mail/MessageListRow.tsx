import { labelColor } from '../../lib/taxonomy'
import { extractDisplayName, timeAgo, truncate } from '../../lib/format'
import { Avatar } from '../ui/Avatar'

interface Props {
  subject?: string | null
  sender?: string | null
  timestamp?: number | null
  label?: string | null
  snippet?: string | null
  active?: boolean
  onClick: () => void
  checkbox?: React.ReactNode
}

export function MessageListRow({ subject, sender, timestamp, label, snippet, active, onClick, checkbox }: Props) {
  const color = labelColor(label)
  return (
    <div
      onClick={onClick}
      className={
        'flex cursor-pointer items-center gap-2.5 border-b border-border px-3 py-2.5 transition-colors ' +
        (active ? 'bg-accent-soft' : 'hover:bg-surface-hover')
      }
    >
      {checkbox}
      <Avatar sender={sender} size={30} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate text-[12.5px] font-semibold text-text">{extractDisplayName(sender)}</span>
          <span className="shrink-0 text-[10.5px] text-text-faint">{timeAgo(timestamp)}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ background: color }}
          />
          <span className="truncate text-[12px] text-text-muted">{truncate(subject, 60) || '[No Subject]'}</span>
        </div>
        {snippet && <div className="truncate text-[11px] text-text-faint">{snippet}</div>}
      </div>
    </div>
  )
}
