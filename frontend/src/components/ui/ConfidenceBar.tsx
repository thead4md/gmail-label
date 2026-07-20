import { confidenceColor } from '../../lib/taxonomy'

export function ConfidenceBar({ value }: { value?: number | null }) {
  if (value === null || value === undefined) return null
  const pct = Math.round(value * 100)
  const color = confidenceColor(value)
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="h-1 w-14 overflow-hidden rounded-full bg-border-strong">
        <span className="block h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
      </span>
      <span className="text-[11px] font-semibold tabular-nums" style={{ color }}>
        {pct}%
      </span>
    </span>
  )
}
