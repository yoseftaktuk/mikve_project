import type { ReactNode } from 'react'
import styles from './PageShell.module.css'
import type { PageShellVariant } from './usePageShell'

type PageShellProps = {
  children: ReactNode
  variant?: PageShellVariant
  className?: string
}

export function PageShell({ children, variant = 'default', className }: PageShellProps) {
  const variantClass =
    variant === 'centered'
      ? styles.centered
      : variant === 'grid'
        ? styles.grid
        : variant === 'compactGrid'
          ? styles.compactGrid
          : styles.default

  return (
    <div className={[styles.shell, variantClass, className].filter(Boolean).join(' ')}>
      {children}
    </div>
  )
}

export { styles as pageShellStyles }
