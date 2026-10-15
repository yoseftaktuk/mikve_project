import type { PendingApproval } from '../../types/fingerprint'
import styles from './AccessApprovalDialog.module.css'
import { useAccessApprovalDialog } from './useAccessApprovalDialog'

type AccessApprovalDialogProps = {
  approval: PendingApproval
  formatMoney: (cents: number) => string
  submitting: boolean
  error?: string | null
  onApprove: () => void
  onCancel: () => void
}

/** Confirmation popup shown after a fingerprint scan, before any money is charged. */
export function AccessApprovalDialog({
  approval,
  formatMoney,
  submitting,
  error,
  onApprove,
  onCancel,
}: AccessApprovalDialogProps) {
  const { secondsLeft, displayName, balanceAfterCents, isUrgent } = useAccessApprovalDialog({ approval })

  return (
    <div className={styles.overlay}>
      <div className={styles.dialog} role="dialog" aria-modal="true" aria-live="assertive">
        <div className={styles.icon}>🫆</div>
        <h3 className={styles.name}>{displayName}</h3>
        <p className={styles.subtitle}>טביעת אצבע זוהתה — נדרש אישור לתשלום</p>

        <div className={styles.fee}>{formatMoney(approval.feeCents)}</div>
        <div className={styles.amounts}>
          <div className={styles.amountRow}>
            <span>יתרה נוכחית</span>
            <b>{formatMoney(approval.balanceCents)}</b>
          </div>
          <div className={styles.amountRow}>
            <span>יתרה לאחר התשלום</span>
            <b>{formatMoney(balanceAfterCents)}</b>
          </div>
        </div>

        <p className={`${styles.countdown} ${isUrgent ? styles.countdownUrgent : ''}`}>
          {secondsLeft > 0 ? `האישור יפוג בעוד ${secondsLeft} שניות` : 'האישור פג — סרוק שוב'}
        </p>

        <div className={styles.actions}>
          <button
            type="button"
            className={styles.confirmButton}
            disabled={submitting || secondsLeft === 0}
            onClick={onApprove}
          >
            {submitting ? 'מבצע…' : 'שלם ופתח דלת'}
          </button>
          <button type="button" className={styles.cancelButton} disabled={submitting} onClick={onCancel}>
            ביטול
          </button>
        </div>

        {error && <p className={styles.error}>{error}</p>}
      </div>
    </div>
  )
}
