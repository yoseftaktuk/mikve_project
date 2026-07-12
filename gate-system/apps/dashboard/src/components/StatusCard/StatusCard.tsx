import type { ReactNode } from 'react'
import styles from './StatusCard.module.css'
import { useStatusCard } from './useStatusCard'

type StatusCardProps = {
  children: ReactNode
  variant?: 'default' | 'compact' | 'flushTop'
  className?: string
}

export function StatusCard({ children, variant = 'default', className }: StatusCardProps) {
  const { cardClassName } = useStatusCard({ variant, className })

  return <section className={cardClassName}>{children}</section>
}

export { styles as statusCardStyles }
