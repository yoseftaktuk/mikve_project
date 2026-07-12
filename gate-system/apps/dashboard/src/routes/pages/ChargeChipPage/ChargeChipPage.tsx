import { usePageMeta } from '../../../app/pageMeta'
import { CreditCardForm } from '../../../components/CreditCardForm'
import { FormField } from '../../../components/FormField'
import { LoadingSpinner } from '../../../components/LoadingSpinner'
import { PageShell, pageShellStyles } from '../../../components/PageShell'
import { StatusCard } from '../../../components/StatusCard'
import styles from './ChargeChipPage.module.css'
import { useChargeChipPage } from './useChargeChipPage'

export function ChargeChipPage() {
  usePageMeta({
    title: "טעינת צ'יפ",
    subtitle: 'הזן סכום ופרטי כרטיס אשראי',
  })

  const {
    amount,
    card,
    fieldErrors,
    submitError,
    successMessage,
    loading,
    isFormLocked,
    updateCard,
    handleAmountChange,
    onSubmit,
  } = useChargeChipPage()

  return (
    <PageShell variant="centered">
      <div className={pageShellStyles.formWrap}>
        <StatusCard className={styles.chargeCard}>
          <form className={styles.form} onSubmit={onSubmit} noValidate>
            <FormField label="סכום (₪)" htmlFor="amount" error={fieldErrors.amount}>
              <input
                id="amount"
                type="text"
                inputMode="decimal"
                value={amount}
                onChange={(e) => handleAmountChange(e.target.value)}
                className={styles.input}
                placeholder="למשל 10"
                min={1}
                disabled={isFormLocked}
              />
            </FormField>

            <CreditCardForm
              values={card}
              errors={fieldErrors}
              disabled={isFormLocked}
              onChange={updateCard}
            />

            {submitError && (
              <p className={styles.error} role="alert">
                {submitError}
              </p>
            )}

            {successMessage && (
              <p className={styles.successMessage} role="status">
                {successMessage}
              </p>
            )}

            <button type="submit" className={styles.submitButton} disabled={isFormLocked}>
              {loading ? (
                <>
                  <LoadingSpinner size="sm" />
                  <span>מעבד תשלום…</span>
                </>
              ) : (
                'Pay and Charge'
              )}
            </button>
          </form>
        </StatusCard>
      </div>
    </PageShell>
  )
}
