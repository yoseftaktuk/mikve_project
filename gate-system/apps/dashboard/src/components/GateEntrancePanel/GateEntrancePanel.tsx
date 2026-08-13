import { BiCoinStack } from 'react-icons/bi'
import { FaFingerprint } from 'react-icons/fa'
import { formatCompactMoney } from '../../app/money'
import { usePageMetaContext } from '../../app/pageMeta/PageMetaContext'
import styles from './GateEntrancePanel.module.css'
import type { GateStatus } from './types'

type GateEntrancePanelProps = {
  gateStatus: GateStatus | null
  cashProgress: number
  lastActivity: string | null
  formatMoney: (cents: number) => string
}

/** Displays entrance methods, fee, cash progress, and last gate activity. */
export function GateEntrancePanel({
  gateStatus,
  cashProgress,
  lastActivity,
}: GateEntrancePanelProps) {
  const { meta } = usePageMetaContext()

  const paidCents = gateStatus?.cash_accumulated_cents ?? 0
  const feeCents = gateStatus?.entrance_fee_cents ?? 0
  const remainingCents = gateStatus ? Math.max(0, feeCents - paidCents) : 0

  return (
    <section className={styles.panel} aria-labelledby="gate-page-title">
      <div className={styles.intro}>
        {meta.titleInContent && meta.title && (
          <h1 id="gate-page-title" className={styles.pageTitle}>
            {meta.title}
          </h1>
        )}
        {meta.titleInContent && meta.subtitle && (
          <p className={styles.pageSubtitle}>{meta.subtitle}</p>
        )}
      </div>

      <div className={styles.methods}>
        <article className={styles.methodCard}>
          <span className={styles.methodIcon} aria-hidden>
            <BiCoinStack />
          </span>
          <h3 className={styles.methodTitle}>מזומן</h3>
          <p className={styles.methodDesc}>הכנס מטבעות</p>
        </article>
        <article className={styles.methodCard}>
          <span className={styles.methodIcon} aria-hidden>
            <FaFingerprint />
          </span>
          <h3 className={styles.methodTitle}>טביעת אצבע</h3>
          <p className={styles.methodDesc}>הנח את האצבע על החיישן</p>
        </article>
      </div>

      <div className={styles.metrics}>
        {gateStatus ? (
          <>
            <div className={styles.feeBlock}>
              <span className={styles.feeLabel}>דמי כניסה</span>
              <span className={styles.feeValue}>{formatCompactMoney(feeCents)}</span>
            </div>

            <div className={styles.cashSection}>
              <div className={styles.cashHeader}>
                <h3 className={styles.cashTitle}>תשלום</h3>
                <span className={styles.cashAmount}>
                  {formatCompactMoney(paidCents)} / {formatCompactMoney(feeCents)}
                </span>
              </div>
              <div
                className={styles.cashProgressBar}
                role="progressbar"
                aria-valuenow={Math.round(cashProgress)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label="התקדמות תשלום"
              >
                <div className={styles.cashProgressFill} style={{ width: `${cashProgress}%` }} />
              </div>
              {paidCents > 0 && remainingCents > 0 && (
                <p className={styles.cashHint}>
                  שולם: {formatCompactMoney(paidCents)} · נותר: {formatCompactMoney(remainingCents)}
                </p>
              )}
              {remainingCents === 0 && paidCents > 0 && (
                <p className={styles.cashHint}>הסכום הושלם</p>
              )}
            </div>
          </>
        ) : (
          <p className={styles.loading}>טוען נתוני שער…</p>
        )}
      </div>

      {lastActivity && (
        <div className={styles.activity} role="status">
          <span className={styles.activityText}>{lastActivity}</span>
        </div>
      )}
    </section>
  )
}
