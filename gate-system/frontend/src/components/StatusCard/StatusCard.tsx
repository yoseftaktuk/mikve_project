import type { ReactNode } from 'react'
import styles from './StatusCard.module.css'
import { useStatusCard } from './useStatusCard'

type StatusCardProps = {
  children: ReactNode
  className?: string
}

export function StatusCard({ children, className }: StatusCardProps) {
  const { cardClassName } = useStatusCard({ className })

  return <section className={cardClassName}>{children}</section>
}

export { styles as statusCardStyles }
