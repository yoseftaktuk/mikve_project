import { useMemo } from 'react'
import styles from './StatusCard.module.css'

type UseStatusCardParams = {
  className?: string
}

export function useStatusCard({ className }: UseStatusCardParams) {
  const cardClassName = useMemo(
    () => [styles.card, className].filter(Boolean).join(' '),
    [className],
  )

  return { cardClassName }
}
