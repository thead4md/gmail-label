import { labelColor } from '../../lib/taxonomy'

export function LabelChip({ label }: { label?: string | null }) {
  if (!label) return null
  const color = labelColor(label)
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide"
      style={{ color, background: `${color}1E`, border: `1px solid ${color}40` }}
    >
      {label}
    </span>
  )
}
