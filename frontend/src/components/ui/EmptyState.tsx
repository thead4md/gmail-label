export function EmptyState({ icon, title, subtitle }: { icon: string; title: string; subtitle?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
      <div className="text-4xl opacity-80">{icon}</div>
      <div className="text-sm font-medium text-text">{title}</div>
      {subtitle && <div className="max-w-sm text-xs text-text-faint">{subtitle}</div>}
    </div>
  )
}
