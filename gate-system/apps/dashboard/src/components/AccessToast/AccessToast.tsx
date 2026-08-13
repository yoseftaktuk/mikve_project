import styles from './AccessToast.module.css'
import type { AccessToastData } from './types'
import { useAccessToast } from './useAccessToast'

export type { AccessToastData } from './types'

type AccessToastProps = {
  toast: AccessToastData
  formatMoney: (cents: number) => string
  onDismiss: () => void
}

export function AccessToast({ toast, formatMoney, onDismiss }: AccessToastProps) {
  const { isGranted, overlayClassName, toastClassName, icon, defaultBalanceLabel } = useAccessToast({ toast })

  return (
    <div className={overlayClassName} onClick={isGranted ? undefined : onDismiss}>
      <div
        className={toastClassName}
        onClick={(e) => e.stopPropagation()}
        role="alert"
        aria-live="assertive"
      >
        <div className={styles.icon}>{icon}</div>
        <h3 className={styles.title}>{toast.title}</h3>
        {toast.balanceCents != null && (
          <>
            <div className={styles.balance}>{formatMoney(toast.balanceCents)}</div>
            <p className={styles.message}>{toast.balanceLabel ?? defaultBalanceLabel}</p>
          </>
        )}
        <p className={styles.message}>{toast.message}</p>
        {!isGranted && (
          <button type="button" className={styles.dismissButton} onClick={onDismiss}>
            הבנתי
          </button>
        )}
      </div>
    </div>
  )
}
