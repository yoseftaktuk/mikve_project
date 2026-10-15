import { usePageMeta } from '../../../app/pageMeta'
import { CardTopupDialog } from '../../../components/CardTopupDialog'
import { PageShell } from '../../../components/PageShell'
import { StatusCard, statusCardStyles } from '../../../components/StatusCard'
import { useMoneyTopupPage } from '../../../hooks/useMoneyTopupPage'
import styles from './MoneyTopupPage.module.css'

/** Desk screen: wait for a fingerprint, then top up via Nedarim Plus. */
export function MoneyTopupPage() {
  const {
    phase,
    user,
    error,
    lastTopupSuccess,
    identifyReady,
    showCardDialog,
    cardDialogProduct,
    hebrewMonthName,
    subscriptionPriceCents,
    openCardTopup,
    openSubscriptionPurchase,
    closeCardTopup,
    onPaid,
    scanAnother,
    simSlot,
    setSimSlot,
    simLoading,
    simError,
    simulateSlotFromInput,
    simulateUnmatched,
    formatMoney,
  } = useMoneyTopupPage()

  usePageMeta({
    title: 'טעינת יתרה',
    subtitle: 'סריקת אצבע וטעינה באשראי',
  })

  return (
    <PageShell variant="centered">
      <div className={styles.card}>
        <StatusCard>
          {phase === 'waiting' && (
            <div className={styles.progress}>
              <div className={styles.sensorIcon}>🫆</div>
              <h2 className={statusCardStyles.sectionTitle}>ממתין לטביעת אצבע</h2>
              <p className={styles.hint}>
                {identifyReady
                  ? 'הניחו אצבע רשומה על החיישן לזיהוי המשתמש.'
                  : 'מפעיל מצב זיהוי…'}
              </p>
              {error && <p className={styles.error}>{error}</p>}
            </div>
          )}

          {phase === 'failed' && (
            <div className={styles.progress}>
              <div className={`${styles.sensorIcon} ${styles.sensorIconStatic}`}>✕</div>
              <p className={styles.stepMessage}>הזיהוי נכשל</p>
              {error && <p className={styles.error}>{error}</p>}
              <button type="button" className={styles.submitButton} onClick={scanAnother}>
                נסה שוב
              </button>
            </div>
          )}

          {phase === 'identified' && user && (
            <div className={styles.progress}>
              <div className={`${styles.sensorIcon} ${styles.sensorIconStatic}`}>✓</div>
              <h2 className={statusCardStyles.sectionTitle}>משתמש מזוהה</h2>
              <div className={styles.summary}>
                <div className={styles.summaryRow}>
                  <span>שם</span>
                  <b>{user.holderName || '—'}</b>
                </div>
                <div className={styles.summaryRow}>
                  <span>מזהה</span>
                  <b>{user.uid}</b>
                </div>
                {user.slot != null && (
                  <div className={styles.summaryRow}>
                    <span>מזהה טביעה</span>
                    <b>#{user.slot}</b>
                  </div>
                )}
                <div className={styles.summaryRow}>
                  <span>יתרה</span>
                  <b>{formatMoney(user.balanceCents)}</b>
                </div>
                <div className={styles.summaryRow}>
                  <span>מנוי חודשי</span>
                  <b>
                    {user.subscriptionActive
                      ? `פעיל (${user.subscriptionMonthName || user.currentHebrewMonthName || 'החודש'})`
                      : 'אין'}
                  </b>
                </div>
              </div>
              {lastTopupSuccess && <p className={styles.success}>{lastTopupSuccess}</p>}
              <div className={styles.actions}>
                <button type="button" className={styles.primaryButton} onClick={openCardTopup}>
                  טעינה באשראי
                </button>
                <button
                  type="button"
                  className={styles.primaryButton}
                  onClick={openSubscriptionPurchase}
                  disabled={user.subscriptionActive}
                  title={
                    user.subscriptionActive
                      ? 'כבר קיים מנוי פעיל לחודש העברי הנוכחי'
                      : undefined
                  }
                >
                  קניית מנוי חודשי לחודש{' '}
                  {hebrewMonthName || user.currentHebrewMonthName || 'הנוכחי'} —{' '}
                  {formatMoney(subscriptionPriceCents)}
                </button>
                {user.subscriptionActive ? (
                  <p className={styles.hint}>כבר קיים מנוי פעיל לחודש העברי הנוכחי</p>
                ) : null}
                <button type="button" className={styles.submitButton} onClick={scanAnother}>
                  סרוק אצבע אחרת
                </button>
              </div>
            </div>
          )}
        </StatusCard>

        <details className={styles.devCard}>
          <summary>כלי פיתוח (סימולציה)</summary>
          <div className={styles.devBody}>
            <label className={styles.simSlotField}>
              מזהה טביעה (slot)
              <input
                type="number"
                min={0}
                max={1000}
                inputMode="numeric"
                value={simSlot}
                onChange={(e) => setSimSlot(e.target.value)}
                className={styles.simSlotInput}
                disabled={simLoading}
              />
            </label>
            <div className={styles.devButtons}>
              <button type="button" disabled={simLoading} onClick={() => void simulateSlotFromInput()}>
                {simLoading ? 'מריץ…' : 'סימולציית אצבע'}
              </button>
              <button type="button" disabled={simLoading} onClick={simulateUnmatched}>
                אצבע לא מזוהה
              </button>
            </div>
            <p className={styles.simHint}>slot 1 = FP-001 (אחרי רישום אצבע)</p>
            {simError && <p className={styles.devError}>{simError}</p>}
          </div>
        </details>
      </div>

      {showCardDialog && user && (
        <CardTopupDialog
          fingerprintUid={user.uid}
          formatMoney={formatMoney}
          product={cardDialogProduct}
          hebrewMonthName={hebrewMonthName || user.currentHebrewMonthName}
          onClose={closeCardTopup}
          onPaid={onPaid}
        />
      )}
    </PageShell>
  )
}
