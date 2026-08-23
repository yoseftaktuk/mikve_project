import { useEffect, useRef } from 'react'
import { usePageMeta } from '../../../app/pageMeta'
import { formatMoney } from '../../../app/money'
import { PageShell } from '../../../components/PageShell'
import { StatusCard, statusCardStyles } from '../../../components/StatusCard'
import { useManagementPricing } from '../../../hooks/useManagementPricing'
import { useManagementStats } from '../../../hooks/useManagementStats'
import { useManagementUsers } from '../../../hooks/useManagementUsers'
import styles from './ManagementPage.module.css'
import { ManagementStatisticsSection } from './ManagementStatisticsSection'
import { useManagementPage } from './useManagementPage'

/** PIN-protected management page for door open and registered users. */
export function ManagementPage() {
  const {
    authenticated,
    authChecking,
    pin,
    setPin,
    pinError,
    pinLoading,
    actionError,
    actionSuccess,
    loading,
    gateStatus,
    onPinSubmit,
    logout,
    openDoor,
  } = useManagementPage()

  const users = useManagementUsers(authenticated)
  const stats = useManagementStats(authenticated)
  const pricing = useManagementPricing(authenticated)
  const editFormRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!users.editingId) return
    editFormRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [users.editingId])

  usePageMeta(
    authenticated
      ? { title: 'ניהול', subtitle: 'פתיחת דלת, מחירים, רשומים וסטטיסטיקה' }
      : { title: 'ניהול', subtitle: 'הזן קוד סודי לכניסה' },
  )

  if (authChecking) {
    return (
      <PageShell variant="centered">
        <StatusCard className={styles.pinCard}>
          <p className={statusCardStyles.hint}>בודק הרשאה…</p>
        </StatusCard>
      </PageShell>
    )
  }

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

  const bannerError = actionError || users.error || pricing.error
  const bannerSuccess = actionSuccess || users.success || pricing.success

  return (
    <PageShell variant="default" className={styles.managementShell}>
      <div className={styles.toolbar}>
        <button type="button" className={styles.logoutButton} onClick={logout}>
          יציאה
        </button>
      </div>

      <StatusCard className={styles.doorCard}>
        <h2 className={statusCardStyles.sectionTitle}>פתיחת דלת</h2>
        <p className={statusCardStyles.hint}>
          {gateStatus?.door_unlock_seconds ?? '…'} שניות
        </p>
        <button type="button" className={styles.submitButton} disabled={loading} onClick={() => void openDoor()}>
          פתח דלת
        </button>
      </StatusCard>

      <StatusCard className={styles.pricingCard}>
        <div className={styles.usersHeader}>
          <h2 className={statusCardStyles.sectionTitle}>מחירים</h2>
          <button
            type="button"
            className={styles.refreshButton}
            disabled={pricing.loading || pricing.saving}
            onClick={() => void pricing.refresh()}
          >
            {pricing.loading ? 'טוען…' : 'רענן'}
          </button>
        </div>
        <p className={statusCardStyles.hint}>מחיר כניסה ומנוי חודשי (בשקלים)</p>
        <label className={styles.formField}>
          מחיר כניסה (₪)
          <input
            value={pricing.entranceShekels}
            onChange={(e) => pricing.setEntranceShekels(e.target.value)}
            inputMode="decimal"
            className={styles.input}
            disabled={pricing.loading || pricing.saving}
          />
        </label>
        <label className={styles.formField}>
          מחיר מנוי חודשי (₪)
          <input
            value={pricing.subscriptionShekels}
            onChange={(e) => pricing.setSubscriptionShekels(e.target.value)}
            inputMode="decimal"
            className={styles.input}
            disabled={pricing.loading || pricing.saving}
          />
        </label>
        <button
          type="button"
          className={styles.submitButton}
          disabled={pricing.loading || pricing.saving}
          onClick={() => void pricing.save()}
        >
          {pricing.saving ? 'שומר…' : 'שמור מחירים'}
        </button>
      </StatusCard>

      <StatusCard className={styles.statsCard}>
        <ManagementStatisticsSection
          range={stats.range}
          onRangeChange={stats.setRange}
          stats={stats.stats}
          loading={stats.loading}
          error={stats.error}
          onRefresh={() => void stats.refresh()}
        />
      </StatusCard>

      <StatusCard className={styles.usersCard}>
        <div className={styles.usersHeader}>
          <h2 className={statusCardStyles.sectionTitle}>רשומים</h2>
          <button
            type="button"
            className={styles.refreshButton}
            disabled={users.loading || users.saving}
            onClick={() => void users.refresh()}
          >
            {users.loading ? 'טוען…' : 'רענן'}
          </button>
        </div>

        {users.users.length === 0 && !users.loading && (
          <p className={statusCardStyles.hint}>אין רשומים עדיין. רשום אצבע בדף רישום אצבע.</p>
        )}

        <div className={statusCardStyles.scrollBody}>
          <ul className={styles.userList}>
            {users.users.map((user) => {
              const isEditing = users.editingId === user.fingerprint_id
              return (
                <li key={user.fingerprint_id} className={styles.userRow}>
                  {isEditing ? (
                    <div className={styles.editForm} ref={editFormRef}>
                      <label className={styles.formField}>
                        שם
                        <input
                          value={users.editName}
                          onChange={(e) => users.setEditName(e.target.value)}
                          className={styles.input}
                          disabled={users.saving}
                        />
                      </label>
                      <label className={styles.formField}>
                        תעודת זהות
                        <input
                          value={users.editNationalId}
                          onChange={(e) =>
                            users.setEditNationalId(e.target.value.replace(/\D/g, '').slice(0, 9))
                          }
                          inputMode="numeric"
                          className={styles.input}
                          disabled={users.saving}
                          autoComplete="off"
                        />
                      </label>
                      <label className={styles.formField}>
                        יתרה (₪)
                        <input
                          value={users.editBalanceShekels}
                          onChange={(e) => users.setEditBalanceShekels(e.target.value)}
                          inputMode="decimal"
                          className={styles.input}
                          placeholder="0"
                          disabled={users.saving}
                        />
                      </label>
                      <label className={styles.checkboxField}>
                        <input
                          type="checkbox"
                          checked={users.editEnabled}
                          onChange={(e) => users.setEditEnabled(e.target.checked)}
                          disabled={users.saving}
                        />
                        פעיל (מורשה לכניסה)
                      </label>
                      <div className={styles.actions}>
                        <button type="button" disabled={users.saving} onClick={() => void users.saveEdit()}>
                          {users.saving ? 'שומר…' : 'שמור'}
                        </button>
                        <button type="button" disabled={users.saving} onClick={users.cancelEdit}>
                          ביטול
                        </button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className={styles.userMeta}>
                        <div className={styles.userName}>
                          {user.holder_name?.trim() || 'ללא שם'}
                          {!user.is_enabled && <span className={styles.badgeDisabled}>חסום</span>}
                        </div>
                        <div className={styles.userSub}>
                          {user.national_id ? `${user.national_id} · ` : ''}
                          {user.uid} · {formatMoney(user.balance_cents)}
                        </div>
                      </div>
                      <div className={styles.actions}>
                        <button
                          type="button"
                          disabled={users.saving}
                          onClick={() => users.startEdit(user)}
                        >
                          עריכה
                        </button>
                        <button
                          type="button"
                          className={styles.dangerButton}
                          disabled={users.saving}
                          onClick={() => void users.deleteUser(user)}
                        >
                          מחק
                        </button>
                      </div>
                    </>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      </StatusCard>

      {bannerError && <p className={styles.errorBanner}>{bannerError}</p>}
      {bannerSuccess && <p className={styles.successBanner}>{bannerSuccess}</p>}
    </PageShell>
  )
}
