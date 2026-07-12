import styles from './LoadingSpinner.module.css'
import { useLoadingSpinner } from './useLoadingSpinner'

type LoadingSpinnerProps = {
  size?: 'sm' | 'md'
  label?: string
}

export function LoadingSpinner({ size = 'md', label = 'טוען…' }: LoadingSpinnerProps) {
  const { className } = useLoadingSpinner({ size })

  return (
    <span className={className} role="status" aria-label={label}>
      <span className={styles.ring} aria-hidden />
    </span>
  )
}
