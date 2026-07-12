import { usePageMeta } from '../../../app/pageMeta'
import { ChipToast } from '../../../components/ChipToast'
import { GateEntrancePanel } from '../../../components/GateEntrancePanel'
import { PageShell } from '../../../components/PageShell'
import styles from './DashboardPage.module.css'
import { useDashboardPage } from './useDashboardPage'

export function DashboardPage() {
  usePageMeta({
    title: 'שער כניסה',
    subtitle: 'בחר איך להיכנס — צ\'יפ או מזומן',
    titleInContent: true,
    showLiveStatus: true,
  })

  const {
    gateStatus,
    chipToast,
    lastActivity,
    simError,
    simLoading,
    cashProgress,
    dismissChipToast,
    simulateChip,
    simulateCash,
    formatMoney,
  } = useDashboardPage()

  return (
    <>
      {chipToast && (
        <ChipToast toast={chipToast} formatMoney={formatMoney} onDismiss={dismissChipToast} />
      )}

      <PageShell variant="default">
        <div className={styles.page}>
          <GateEntrancePanel
            gateStatus={gateStatus}
            cashProgress={cashProgress}
            lastActivity={lastActivity}
            formatMoney={formatMoney}
          />

          <details className={styles.devCard}>
            <summary>כלי פיתוח (סימולציה)</summary>
            <div className={styles.devBody}>
              <div className={styles.devButtons}>
                <button type="button" disabled={simLoading} onClick={() => void simulateChip()}>
                  {simLoading ? 'מריץ…' : "סימולציית צ'יפ"}
                </button>
                <button type="button" disabled={simLoading} onClick={() => void simulateCash(100)}>
                  הוסף ₪1
                </button>
                <button
                  type="button"
                  disabled={simLoading || !gateStatus}
                  onClick={() => gateStatus && void simulateCash(gateStatus.entrance_fee_cents)}
                >
                  {gateStatus ? `שלם ${formatMoney(gateStatus.entrance_fee_cents)}` : 'שלם עלות מלאה'}
                </button>
              </div>
              {simError && <p className={styles.devError}>{simError}</p>}
            </div>
          </details>
        </div>
      </PageShell>
    </>
  )
}
