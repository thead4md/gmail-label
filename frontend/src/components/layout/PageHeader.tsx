import type { ReactNode } from 'react'

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border bg-bg/85 px-6 py-4 backdrop-blur-sm">
      <div>
        <h1 className="text-[15px] font-bold">{title}</h1>
        {subtitle && <p className="text-xs text-text-faint">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
