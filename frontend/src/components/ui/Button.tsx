import type { ButtonHTMLAttributes } from 'react'
import clsx from 'clsx'

type Variant = 'default' | 'primary' | 'success' | 'danger' | 'ghost'

const VARIANT_CLASSES: Record<Variant, string> = {
  default:
    'bg-surface-2 border border-border text-text hover:bg-surface-3 hover:border-border-strong',
  primary: 'bg-accent border border-accent text-white hover:bg-accent-hover',
  success:
    'bg-success-soft border border-success/40 text-success hover:bg-success/20',
  danger: 'bg-danger-soft border border-danger/40 text-danger hover:bg-danger/20',
  ghost: 'border border-transparent text-text-muted hover:bg-surface-2 hover:text-text',
}

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: 'sm' | 'md'
}

export function Button({ variant = 'default', size = 'md', className, ...rest }: Props) {
  return (
    <button
      className={clsx(
        'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-50',
        size === 'sm' ? 'px-2.5 py-1 text-xs' : 'px-3.5 py-1.5 text-[13px]',
        VARIANT_CLASSES[variant],
        className,
      )}
      {...rest}
    />
  )
}
