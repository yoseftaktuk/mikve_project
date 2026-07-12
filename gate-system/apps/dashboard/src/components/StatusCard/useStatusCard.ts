import { useMemo } from 'react'
import styles from './StatusCard.module.css'

type StatusCardVariant = 'default' | 'compact' | 'flushTop'

type UseStatusCardParams = {
  variant?: StatusCardVariant
  className?: string
}

export function useStatusCard({ variant = 'default', className }: UseStatusCardParams) {
  const cardClassName = useMemo(() => {
    const variantClass =
      variant === 'compact' ? styles.cardCompact : variant === 'flushTop' ? styles.cardFlushTop : ''
    return [styles.card, variantClass, className].filter(Boolean).join(' ')
  }, [variant, className])

  return { cardClassName }
}
