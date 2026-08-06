import { usePageMeta } from '../../../app/pageMeta'
import { AccessApprovalDialog } from '../../../components/AccessApprovalDialog'
import { CardTopupDialog } from '../../../components/CardTopupDialog'
import { ChipToast } from '../../../components/ChipToast'
import { GateEntrancePanel } from '../../../components/GateEntrancePanel'
import { PageShell } from '../../../components/PageShell'
import { TopupChoiceDialog } from '../../../components/TopupChoiceDialog'
import styles from './DashboardPage.module.css'
import { useDashboardPage } from './useDashboardPage'

/** Entrance dashboard with live gate status and development simulators. */
export function DashboardPage() {
  usePageMeta({
    title: 'שער כניסה',
    subtitle: 'בחר איך להיכנס — טביעת אצבע או מזומן',
    titleInContent: true,
    showLiveStatus: true,
  })

  const {
    gateStatus,
    chipToast,
    lastActivity,
    simError,
    simLoading,
    simSlot,
    setSimSlot,
    cashProgress,
    pendingApproval,
    approvalSubmitting,
    approvalError,
    topupOffer,
    cardTopupOpen,
    dismissChipToast,
    simulateSlotFromInput,
    simulateUnmatched,
    simulateCash,
    approvePending,
    cancelPending,
    dismissTopupOffer,
    chooseCoinsTopup,
    chooseCardTopup,
    onCardTopupPaid,
    formatMoney,
  } = useDashboardPage()

  const blockingDialog = Boolean(pendingApproval || topupOffer)

  return (
    <>
      {pendingApproval && (
        <AccessApprovalDialog
          approval={pendingApproval}
          formatMoney={formatMoney}
          submitting={approvalSubmitting}
          error={approvalError}
          onApprove={() => void approvePending()}
          onCancel={() => void cancelPending()}
        />
      )}

      {topupOffer && !cardTopupOpen && (
        <TopupChoiceDialog
          offer={topupOffer}
          formatMoney={formatMoney}
          onCoins={chooseCoinsTopup}
          onCreditCard={chooseCardTopup}
          onCancel={dismissTopupOffer}
        />
      )}

      {topupOffer && cardTopupOpen && (
        <CardTopupDialog
          chipUid={topupOffer.uid}
          formatMoney={formatMoney}
          onClose={dismissTopupOffer}
          onPaid={onCardTopupPaid}
        />
      )}

      {!blockingDialog && chipToast && (
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
              <p className={styles.simHint}>slot 1 = FP-001 (אחרי רישום אצבע)</p>
              {simError && <p className={styles.devError}>{simError}</p>}
            </div>
          </details>
        </div>
      </PageShell>
    </>
  )
}
