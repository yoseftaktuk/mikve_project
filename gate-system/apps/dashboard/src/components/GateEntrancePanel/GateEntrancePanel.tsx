import { usePageMetaContext } from '../../app/pageMeta/PageMetaContext'
import styles from './GateEntrancePanel.module.css'
import type { GateStatus } from './types'

type GateEntrancePanelProps = {
  gateStatus: GateStatus | null
  cashProgress: number
  lastActivity: string | null
  formatMoney: (cents: number) => string
}

export function GateEntrancePanel({
  gateStatus,
  cashProgress,
  lastActivity,
  formatMoney,
}: GateEntrancePanelProps) {
  const { meta } = usePageMetaContext()

  const remainingCents = gateStatus
    ? Math.max(0, gateStatus.entrance_fee_cents - gateStatus.cash_accumulated_cents)
    : 0

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

        <p className={styles.lead}>
          בחר אחת משתי הדרכים. כשהתשלום מתקבל — הדלת תיפתח אוטומטית
          {gateStatus ? ` ל-${gateStatus.door_unlock_seconds} שניות` : ''}.
        </p>
      </div>

      <div className={styles.methods}>
        <article className={styles.methodCard}>
          <span className={styles.methodIcon} aria-hidden>
            💳
          </span>
          <h3 className={styles.methodTitle}>כניסה בצ&apos;יפ</h3>
          <p className={styles.methodDesc}>הצמד את הצ&apos;יפ לקורא. העלות תיגבה מהיתרה.</p>
        </article>
        <article className={styles.methodCard}>
          <span className={styles.methodIcon} aria-hidden>
            🪙
          </span>
          <h3 className={styles.methodTitle}>כניסה במזומן</h3>
          <p className={styles.methodDesc}>הכנס מטבעות עד לסכום הנדרש. העודף יוחזר.</p>
        </article>
      </div>

      <div className={styles.metrics}>
        {gateStatus ? (
          <>
            <div className={styles.stats}>
              <div className={styles.statBox}>
                <span className={styles.statLabel}>עלות כניסה</span>
                <span className={styles.statValue}>{formatMoney(gateStatus.entrance_fee_cents)}</span>
              </div>
              <div className={styles.statBox}>
                <span className={styles.statLabel}>זמן פתיחת דלת</span>
                <span className={styles.statValue}>
                  {gateStatus.door_unlock_seconds}
                  <span className={styles.statUnit}> שניות</span>
                </span>
              </div>
            </div>

            <div className={styles.cashSection}>
              <div className={styles.cashHeader}>
                <h3 className={styles.cashTitle}>התקדמות תשלום במזומן</h3>
                <span className={styles.cashAmount}>
                  {formatMoney(gateStatus.cash_accumulated_cents)} / {formatMoney(gateStatus.entrance_fee_cents)}
                </span>
              </div>
              <div
                className={styles.cashProgressBar}
                role="progressbar"
                aria-valuenow={Math.round(cashProgress)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label="התקדמות תשלום במזומן"
              >
                <div className={styles.cashProgressFill} style={{ width: `${cashProgress}%` }} />
              </div>
              <p className={styles.cashHint}>
                {remainingCents > 0
                  ? `חסרים עוד ${formatMoney(remainingCents)} לפתיחת הדלת`
                  : 'הסכום המלא הוכנס — ממתין למטבע נוסף או לצ\'יפ'}
              </p>
            </div>
          </>
        ) : (
          <p className={styles.loading}>טוען נתוני שער…</p>
        )}
      </div>

      {lastActivity && (
        <div className={styles.activity} role="status">
          <span className={styles.activityIcon} aria-hidden>
            ℹ️
          </span>
          <span className={styles.activityText}>{lastActivity}</span>
        </div>
      )}
    </section>
  )
}
