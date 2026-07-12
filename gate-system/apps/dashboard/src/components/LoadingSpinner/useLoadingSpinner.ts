import { useMemo } from 'react'
import styles from './LoadingSpinner.module.css'

type SpinnerSize = 'sm' | 'md'

type UseLoadingSpinnerParams = {
  size?: SpinnerSize
}

export function useLoadingSpinner({ size = 'md' }: UseLoadingSpinnerParams) {
  const className = useMemo(() => [styles.spinner, styles[size]].join(' '), [size])

  return { className }
}
