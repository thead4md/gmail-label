import { channelColor, channelIcon } from '../../lib/taxonomy'

export function ChannelChip({ channel }: { channel?: string | null }) {
  if (!channel || channel === 'unknown') return null
  const color = channelColor(channel)
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide"
      style={{ color, background: `${color}1E`, border: `1px solid ${color}40` }}
    >
      <span className="text-xs not-italic">{channelIcon(channel)}</span>
      {channel}
    </span>
  )
}
