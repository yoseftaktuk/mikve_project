import { usePageMeta } from '../../../app/pageMeta'
import { PageShell } from '../../../components/PageShell'
import { StatusCard, statusCardStyles } from '../../../components/StatusCard'
import styles from './FingerprintEnrollPage.module.css'
import { useFingerprintEnrollPage } from './useFingerprintEnrollPage'

/** Desk screen that enrolls a fingerprint and opens a named balance. */
export function FingerprintEnrollPage() {
  const {
    holderName,
    setHolderName,
    nationalId,
    setNationalId,
    initialAmountShekels,
    setInitialAmountShekels,
    enroll,
    error,
    submitting,
    isActive,
    stepMessage,
    stepIndex,
    stepOrder,
    start,
    cancel,
    reset,
    formatMoney,
  } = useFingerprintEnrollPage()

  usePageMeta({
    title: 'רישום טביעת אצבע',
    subtitle: 'רישום נכנס חדש וטעינת יתרה',
  })

  return (
    <PageShell variant="centered">
      <div className={styles.card}>
        <StatusCard>
          {enroll.step === 'idle' && (
            <>
              <h2 className={statusCardStyles.sectionTitle}>נכנס חדש</h2>
              <p className={styles.hint}>
                הרישום יוצר כרטיס אישי לפי טביעת האצבע. אפשר לטעון יתרה התחלתית מיד.
              </p>
              <form onSubmit={start}>
                <label className={styles.formField}>
                  שם מלא
                  <input
                    value={holderName}
                    onChange={(e) => setHolderName(e.target.value)}
                    className={styles.input}
                    placeholder="ישראל ישראלי"
                  />
                </label>
                <label className={styles.formField}>
                  תעודת זהות
                  <input
                    value={nationalId}
                    onChange={(e) => setNationalId(e.target.value.replace(/\D/g, '').slice(0, 9))}
                    inputMode="numeric"
                    className={styles.input}
                    placeholder="123456782"
                    autoComplete="off"
                  />
                </label>
                <label className={styles.formField}>
                  יתרה התחלתית (₪, אופציונלי)
                  <input
                    value={initialAmountShekels}
                    onChange={(e) => setInitialAmountShekels(e.target.value)}
                    inputMode="decimal"
                    className={styles.input}
                    placeholder="50"
                  />
                </label>
                <button type="submit" className={styles.submitButton} disabled={submitting}>
                  {submitting ? 'מתחיל…' : 'התחל רישום'}
                </button>
              </form>
              {error && <p className={styles.error}>{error}</p>}
            </>
          )}

          {isActive && (
            <div className={styles.progress}>
              <div className={styles.sensorIcon}>🫆</div>
              <p className={styles.stepMessage}>{stepMessage}</p>
              <div className={styles.steps}>
                {stepOrder.map((step, index) => (
                  <span
                    key={step}
                    className={`${styles.stepDot} ${index <= stepIndex ? styles.stepDotDone : ''}`}
                  />
                ))}
              </div>
              <p className={styles.hint}>רושם עבור {enroll.holderName}</p>
              <button type="button" className={styles.submitButton} onClick={() => void cancel()}>
                ביטול
              </button>
            </div>
          )}

          {enroll.step === 'registered' && (
            <div className={styles.progress}>
              <div className={`${styles.sensorIcon} ${styles.sensorIconStatic}`}>✓</div>
              <p className={`${styles.stepMessage} ${styles.success}`}>{stepMessage}</p>
              <div className={styles.summary}>
                <div className={styles.summaryRow}>
                  <span>שם</span>
                  <b>{enroll.holderName}</b>
                </div>
                {enroll.nationalId && (
                  <div className={styles.summaryRow}>
                    <span>תעודת זהות</span>
                    <b>{enroll.nationalId}</b>
                  </div>
                )}
                {enroll.slot != null && (
                  <div className={styles.summaryRow}>
                    <span>מזהה טביעה</span>
                    <b>#{enroll.slot}</b>
                  </div>
                )}
                {enroll.balanceCents != null && (
                  <div className={styles.summaryRow}>
                    <span>יתרה</span>
                    <b>{formatMoney(enroll.balanceCents)}</b>
                  </div>
                )}
              </div>
              <button type="button" className={styles.submitButton} onClick={reset}>
                רישום נוסף
              </button>
            </div>
          )}

          {!isActive && enroll.step !== 'idle' && enroll.step !== 'registered' && (
            <div className={styles.progress}>
              <div className={`${styles.sensorIcon} ${styles.sensorIconStatic}`}>
                {enroll.step === 'duplicate' ? '🫆' : '✕'}
              </div>
              <p
                className={`${styles.stepMessage} ${enroll.step === 'duplicate' ? styles.warning : ''}`}
              >
                {stepMessage}
              </p>
              {enroll.step === 'duplicate' && enroll.slot != null && (
                <p className={styles.hint}>מזהה הטביעה הקיימת: #{enroll.slot}</p>
              )}
              {error && <p className={styles.error}>{error}</p>}
              <button type="button" className={styles.submitButton} onClick={reset}>
                חזור להתחלה
              </button>
            </div>
          )}
        </StatusCard>
      </div>
    </PageShell>
  )
}
