import { useCardTopupDialog } from '../../hooks/useCardTopupDialog'
import type { CardTopupProduct } from '../../types/topup'
import styles from './CardTopupDialog.module.css'

type CardTopupDialogProps = {
  fingerprintUid: string
  formatMoney: (cents: number) => string
  product?: CardTopupProduct
  hebrewMonthName?: string | null
  onClose: () => void
  onPaid: (balanceAfterCents: number) => void
}

/** Amount presets or fixed subscription + Nedarim iframe + wait for server confirmation. */
export function CardTopupDialog({
  fingerprintUid,
  formatMoney,
  product = 'balance',
  hebrewMonthName,
  onClose,
  onPaid,
}: CardTopupDialogProps) {
  const {
    phase,
    isSubscription,
    paymentMode,
    amountsCents,
    subscriptionPriceCents,
    hebrewMonthName: resolvedMonthName,
    created,
    status,
    error,
    clientMessage,
    startTopup,
    iframeRef,
    heightPx,
    requestHeight,
    onIframeError,
    validateError,
    pay,
    close,
  } = useCardTopupDialog({ fingerprintUid, product, hebrewMonthName, onClose, onPaid })

  const busy = phase === 'creating' || phase === 'submitting' || phase === 'waiting_server'
  const isMock = paymentMode === 'mock'
  const monthLabel = resolvedMonthName || 'החודש הנוכחי'

  return (
    <div className={styles.overlay}>
      <div className={styles.dialog} role="dialog" aria-modal="true">
        {!isSubscription && (phase === 'choose_amount' || phase === 'creating') ? (
          <>
            <h3 className={styles.title}>בחר סכום לטעינה</h3>
            <p className={styles.subtitle}>הסכום ייטען ליתרה לאחר אישור התשלום</p>
            <div className={styles.presets}>
              {amountsCents.map((cents) => (
                <button
                  key={cents}
                  type="button"
                  className={styles.presetButton}
                  disabled={busy}
                  onClick={() => void startTopup(cents)}
                >
                  {formatMoney(cents)}
                </button>
              ))}
            </div>
            {error && <p className={styles.error}>{error}</p>}
            <div className={styles.actions}>
              <button type="button" className={styles.cancelButton} disabled={busy} onClick={() => void close()}>
                חזרה
              </button>
            </div>
          </>
        ) : null}

        {isSubscription && phase === 'creating' ? (
          <>
            <h3 className={styles.title}>קניית מנוי חודשי לחודש {monthLabel}</h3>
            <p className={styles.subtitle}>מכין מסך תשלום…</p>
            {error && <p className={styles.error}>{error}</p>}
            <div className={styles.actions}>
              <button type="button" className={styles.cancelButton} onClick={() => void close()}>
                ביטול
              </button>
            </div>
          </>
        ) : null}

        {(phase === 'ready' || phase === 'submitting' || phase === 'waiting_server') && created ? (
          <>
            <h3 className={styles.title}>
              {isSubscription
                ? `קניית מנוי חודשי לחודש ${monthLabel}`
                : 'תשלום בכרטיס אשראי'}
              {isMock ? <span className={styles.mockBadge}> (dev mock)</span> : null}
            </h3>
            <p className={styles.subtitle}>
              {isSubscription
                ? `סכום לתשלום: ${formatMoney(created.amount_cents || subscriptionPriceCents)}`
                : `סכום לטעינה: ${formatMoney(created.amount_cents)}`}
            </p>
            {isMock ? (
              <p className={styles.mockHint}>
                מצב פיתוח — אין חיוב אמיתי. לחץ למטה לסימולציית תשלום
                {isSubscription ? ' והפעלת המנוי.' : ' וטעינת יתרה.'}
              </p>
            ) : (
              <div className={styles.frameWrap}>
                <iframe
                  ref={iframeRef}
                  title="Nedarim Plus"
                  className={styles.frame}
                  style={{ height: heightPx > 0 ? heightPx : 280 }}
                  src={created.iframe_url}
                  scrolling="no"
                  onLoad={() => requestHeight()}
                  onError={onIframeError}
                />
              </div>
            )}
            {isSubscription ? (
              <p className={styles.disclaimer}>
                המנוי מתייחס לחודש העברי {monthLabel} ומתאפס בתחילת החודש העברי הבא, בלי קשר למועד
                הרכישה. הכניסה הראשונה בכל יום כלולה במנוי; כניסות נוספות באותו יום יחויבו מיתרה צבורה
                במחיר כניסה רגיל.
              </p>
            ) : null}
            {clientMessage && <p className={styles.status}>{clientMessage}</p>}
            {(validateError || error) && <p className={styles.error}>{validateError || error}</p>}
            <div className={styles.actions}>
              <button
                type="button"
                className={styles.payButton}
                disabled={busy}
                onClick={() => void pay()}
              >
                {phase === 'submitting'
                  ? 'מעבד תשלום…'
                  : phase === 'waiting_server'
                    ? 'מאשר מול השרת…'
                    : isMock
                      ? 'סימולציית תשלום'
                      : isSubscription
                        ? 'שלם והפעל מנוי'
                        : 'שלם וטען יתרה'}
              </button>
              <button type="button" className={styles.cancelButton} disabled={phase === 'submitting'} onClick={() => void close()}>
                ביטול
              </button>
            </div>
          </>
        ) : null}

        {phase === 'paid' ? (
          <>
            <h3 className={styles.title}>
              {isSubscription ? 'המנוי הופעל בהצלחה' : 'התשלום בוצע בהצלחה'}
            </h3>
            {created ? (
              <p className={styles.subtitle}>
                {isSubscription
                  ? `מנוי לחודש ${monthLabel}: ${formatMoney(created.amount_cents)}`
                  : `נטען: ${formatMoney(created.amount_cents)}`}
              </p>
            ) : null}
            {!isSubscription ? (
              <p className={styles.success}>
                יתרה חדשה: {formatMoney(status?.balance_after_cents ?? 0)}
              </p>
            ) : (
              <p className={styles.success}>המנוי פעיל עד תחילת החודש העברי הבא</p>
            )}
            <p className={styles.subtitle}>
              {isSubscription
                ? 'ניתן להיכנס פעם אחת ביום ללא חיוב יתרה'
                : 'היתרה עודכנה ומוכנה לשימוש'}
            </p>
            <div className={styles.actions}>
              <button type="button" className={styles.payButton} onClick={onClose}>
                סגור
              </button>
            </div>
          </>
        ) : null}

        {phase === 'failed' ? (
          <>
            <h3 className={styles.title}>{isSubscription ? 'רכישת המנוי לא הושלמה' : 'הטעינה לא הושלמה'}</h3>
            {error && <p className={styles.error}>{error}</p>}
            <div className={styles.actions}>
              <button type="button" className={styles.cancelButton} onClick={() => void close()}>
                סגור
              </button>
            </div>
          </>
        ) : null}
      </div>
    </div>
  )
}
