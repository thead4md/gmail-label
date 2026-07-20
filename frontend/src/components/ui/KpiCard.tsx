interface Props {
  icon: string
  label: string
  value: number
  delta?: number | null
}

export function KpiCard({ icon, label, value, delta }: Props) {
  return (
    <div className="flex flex-col gap-2 rounded-xl border border-border bg-gradient-to-br from-surface to-surface-2 px-4 py-3.5 shadow-[var(--shadow-sm)]">
      <div className="flex items-center gap-2">
        <span className="text-base leading-none">{icon}</span>
        <span className="truncate text-[10px] font-bold uppercase tracking-wider text-text-muted">{label}</span>
      </div>
      <div className="text-[26px] font-bold leading-none tabular-nums">{value}</div>
      {delta !== null && delta !== undefined && (
        <div
          className={
            'text-[11px] font-semibold ' +
            (delta > 0 ? 'text-success' : delta < 0 ? 'text-danger' : 'text-text-faint')
          }
        >
          {delta > 0 ? `▲ +${delta} vs yesterday` : delta < 0 ? `▼ ${delta} vs yesterday` : '→ no change'}
        </div>
      )}
    </div>
  )
}
