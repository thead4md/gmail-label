import { trustColor } from '../../lib/taxonomy'

const ICONS: Record<string, string> = { trusted: '✓', neutral: '·', watchlist: '⚠' }

export function TrustBadge({ tier }: { tier?: string | null }) {
  const t = (tier || 'neutral').toLowerCase()
  const color = trustColor(t)
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold"
      style={{ color, background: `${color}1E`, border: `1px solid ${color}40` }}
    >
      {ICONS[t] ?? '·'} {t}
    </span>
  )
}
