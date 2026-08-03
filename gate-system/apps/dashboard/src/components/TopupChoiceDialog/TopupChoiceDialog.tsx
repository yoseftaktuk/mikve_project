import { useTopupChoiceDialog } from '../../hooks/useTopupChoiceDialog'
import type { TopupOffer } from '../../types/topup'
import styles from './TopupChoiceDialog.module.css'

type TopupChoiceDialogProps = {
  offer: TopupOffer
  formatMoney: (cents: number) => string
  onCoins: () => void
  onCreditCard: () => void
  onCancel: () => void
}

/** Shown after a fingerprint scan when the balance cannot cover the entrance fee. */
export function TopupChoiceDialog({
  offer,
  formatMoney,
  onCoins,
  onCreditCard,
  onCancel,
}: TopupChoiceDialogProps) {
  const { displayName, shortfallCents } = useTopupChoiceDialog({ offer })

  return (
    <div className={styles.overlay}>
      <div className={styles.dialog} role="dialog" aria-modal="true" aria-live="assertive">
        <h3 className={styles.name}>{displayName}</h3>
        <p className={styles.subtitle}>אין מספיק יתרה לכניסה — בחר איך לטעון</p>

        <div className={styles.amounts}>
          <div className={styles.amountRow}>
            <span>יתרה נוכחית</span>
            <b>{formatMoney(offer.balanceCents)}</b>
          </div>
          <div className={styles.amountRow}>
            <span>עלות כניסה</span>
            <b>{formatMoney(offer.feeCents)}</b>
          </div>
          <div className={styles.amountRow}>
            <span>חסר</span>
            <b className={styles.shortfall}>{formatMoney(shortfallCents)}</b>
          </div>
        </div>

        <div className={styles.actions}>
          <button type="button" className={styles.primaryButton} onClick={onCreditCard}>
            טעינה בכרטיס אשראי
          </button>
          <button type="button" className={styles.secondaryButton} onClick={onCoins}>
            תשלום במטבעות
          </button>
          <button type="button" className={styles.cancelButton} onClick={onCancel}>
            ביטול
          </button>
        </div>
      </div>
    </div>
  )
}
