import { useMemo } from 'react'
import styles from './ChipToast.module.css'
import type { ChipToastData } from './types'

type UseChipToastParams = {
  toast: ChipToastData
}

export function useChipToast({ toast }: UseChipToastParams) {
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
  const defaultBalanceLabel = "יתרה נותרת בצ'יפ"

  return {
    isGranted,
    overlayClassName,
    toastClassName,
    icon,
    defaultBalanceLabel,
  }
}
