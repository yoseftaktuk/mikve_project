import { useCardTopupDialog } from '../../hooks/useCardTopupDialog'
import styles from './CardTopupDialog.module.css'

type CardTopupDialogProps = {
  chipUid: string
  formatMoney: (cents: number) => string
  onClose: () => void
  onPaid: (balanceAfterCents: number) => void
}

/** Amount presets + Nedarim iframe + wait for server-confirmed credit. */
export function CardTopupDialog({ chipUid, formatMoney, onClose, onPaid }: CardTopupDialogProps) {
  const {
    phase,
    paymentMode,
    amountsCents,
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
  } = useCardTopupDialog({ chipUid, onClose, onPaid })

  const busy = phase === 'creating' || phase === 'submitting' || phase === 'waiting_server'
  const isMock = paymentMode === 'mock'

  return (
    <div className={styles.overlay}>
      <div className={styles.dialog} role="dialog" aria-modal="true">
        {phase === 'choose_amount' || phase === 'creating' ? (
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

        {(phase === 'ready' || phase === 'submitting' || phase === 'waiting_server') && created ? (
          <>
            <h3 className={styles.title}>
              תשלום בכרטיס אשראי
              {isMock ? <span className={styles.mockBadge}> (dev mock)</span> : null}
            </h3>
            <p className={styles.subtitle}>סכום לטעינה: {formatMoney(created.amount_cents)}</p>
            {isMock ? (
              <p className={styles.mockHint}>
                מצב פיתוח — אין חיוב אמיתי. לחץ למטה לסימולציית תשלום וטעינת יתרה.
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
            <h3 className={styles.title}>היתרה נטענה</h3>
            <p className={styles.success}>
              יתרה חדשה: {formatMoney(status?.balance_after_cents ?? 0)}
            </p>
            <p className={styles.subtitle}>ניתן לסרוק שוב את טביעת האצבע לכניסה</p>
            <div className={styles.actions}>
              <button type="button" className={styles.payButton} onClick={onClose}>
                סגור
              </button>
            </div>
          </>
        ) : null}

        {phase === 'failed' ? (
          <>
            <h3 className={styles.title}>הטעינה לא הושלמה</h3>
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
