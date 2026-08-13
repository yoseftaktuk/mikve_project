import { useMemo } from 'react'
import styles from './AccessToast.module.css'
import type { AccessToastData } from './types'

type UseAccessToastParams = {
  toast: AccessToastData
}

export function useAccessToast({ toast }: UseAccessToastParams) {
  const isGranted = toast.kind === 'granted'

  const overlayClassName = useMemo(
    () => [styles.overlay, isGranted ? styles.overlayGranted : ''].filter(Boolean).join(' '),
    [isGranted],
  )

  const toastClassName = useMemo(
    () => [styles.toast, styles[toast.kind]].join(' '),
    [toast.kind],
  )

  const icon = isGranted ? '✓' : '✕'
  const defaultBalanceLabel = 'יתרה נותרת'

  return {
    isGranted,
    overlayClassName,
    toastClassName,
    icon,
    defaultBalanceLabel,
  }
}
