import { usePageMeta } from '../../../app/pageMeta'
import { PageShell } from '../../../components/PageShell'
import { StatusCard, statusCardStyles } from '../../../components/StatusCard'
import styles from './ManagementPage.module.css'
import { useManagementPage } from './useManagementPage'

export function ManagementPage() {
  const {
    authenticated,
    pin,
    setPin,
    pinError,
    pinLoading,
    uid,
    setUid,
    amountShekels,
    setAmountShekels,
    chipInfo,
    actionError,
    actionSuccess,
    loading,
    gateStatus,
    formatMoney,
    onPinSubmit,
    logout,
    lookupChip,
    topupChip,
    openDoor,
  } = useManagementPage()

  usePageMeta(
    authenticated
      ? { title: 'ניהול', subtitle: 'הטענת צ\'יפ ופתיחת דלת' }
      : { title: 'ניהול', subtitle: 'הזן קוד סודי לכניסה' },
  )

  if (!authenticated) {
    return (
      <PageShell variant="centered">
        <StatusCard className={styles.pinCard}>
          <form onSubmit={onPinSubmit}>
            <label className={styles.formField}>
              קוד סודי
              <input
                type="password"
                inputMode="numeric"
                autoComplete="off"
                value={pin}
                onChange={(e) => setPin(e.target.value)}
                className={`${styles.input} ${styles.inputPin}`}
              />
            </label>
            {pinError && <p className={styles.error}>{pinError}</p>}
            <button type="submit" className={styles.submitButton} disabled={pinLoading || !pin}>
              {pinLoading ? 'בודק…' : 'כניסה'}
            </button>
          </form>
        </StatusCard>
      </PageShell>
    )
  }

  return (
    <PageShell variant="compactGrid">
      <div className={styles.toolbar} style={{ gridColumn: '1 / -1' }}>
        <button type="button" className={styles.logoutButton} onClick={logout}>
          יציאה
        </button>
      </div>

      <StatusCard>
        <h2 className={statusCardStyles.sectionTitle}>הטענת צ&apos;יפ</h2>
        <form onSubmit={topupChip}>
          <label className={styles.formField}>
            מזהה צ&apos;יפ (UID)
            <input
              value={uid}
              onChange={(e) => setUid(e.target.value)}
              className={styles.input}
              placeholder="DEMO-UID-1234"
            />
          </label>
          <label className={styles.formField}>
            סכום (₪)
            <input
              value={amountShekels}
              onChange={(e) => setAmountShekels(e.target.value)}
              inputMode="decimal"
              className={styles.input}
              placeholder="10"
            />
          </label>
          <div className={styles.actions}>
            <button type="button" disabled={loading} onClick={() => void lookupChip()}>
              בדוק יתרה
            </button>
            <button type="submit" disabled={loading}>
              {loading ? 'מבצע…' : "הטען"}
            </button>
          </div>
        </form>
        {chipInfo && (
          <p className={statusCardStyles.hintSpaced}>
            יתרה: <b>{formatMoney(chipInfo.balance_cents)}</b>
          </p>
        )}
      </StatusCard>

      <StatusCard>
        <h2 className={statusCardStyles.sectionTitle}>פתיחת דלת</h2>
        <p className={statusCardStyles.hint}>
          {gateStatus?.door_unlock_seconds ?? '…'} שניות
        </p>
        <button type="button" className={styles.submitButton} disabled={loading} onClick={() => void openDoor()}>
          פתח דלת
        </button>
      </StatusCard>

      {actionError && <p className={styles.errorBanner}>{actionError}</p>}
      {actionSuccess && <p className={styles.successBanner}>{actionSuccess}</p>}
    </PageShell>
  )
}
