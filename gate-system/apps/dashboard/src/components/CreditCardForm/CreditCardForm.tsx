import type { CreditCardFields, FieldErrors } from '../../types/chargeChip'
import { FormField } from '../FormField'
import styles from './CreditCardForm.module.css'
import { useCreditCardForm } from './useCreditCardForm'

type CreditCardFormProps = {
  values: CreditCardFields
  errors: Pick<FieldErrors, 'cardNumber' | 'expiry' | 'cvc' | 'cardholderName'>
  disabled?: boolean
  onChange: (field: keyof CreditCardFields, value: string) => void
}

export function CreditCardForm({ values, errors, disabled, onChange }: CreditCardFormProps) {
  const {
    handleCardholderNameChange,
    handleCardNumberChange,
    handleExpiryChange,
    handleCvcChange,
  } = useCreditCardForm({ onChange })

  return (
    <fieldset className={styles.form} disabled={disabled}>
      <legend className={styles.legend}>פרטי כרטיס אשראי</legend>
      <p className={styles.hint}>תשלום בכרטיס אשראי בלבד</p>

      <FormField label="שם בעל הכרטיס" htmlFor="cardholderName" error={errors.cardholderName}>
        <input
          id="cardholderName"
          type="text"
          autoComplete="cc-name"
          value={values.cardholderName}
          onChange={(e) => handleCardholderNameChange(e.target.value)}
          className={styles.input}
          placeholder="כפי שמופיע על הכרטיס"
        />
      </FormField>

      <FormField label="מספר כרטיס" htmlFor="cardNumber" error={errors.cardNumber}>
        <input
          id="cardNumber"
          type="text"
          inputMode="numeric"
          autoComplete="cc-number"
          value={values.cardNumber}
          onChange={(e) => handleCardNumberChange(e.target.value)}
          className={styles.input}
          placeholder="0000 0000 0000 0000"
        />
      </FormField>

      <div className={styles.row}>
        <FormField label="תוקף" htmlFor="expiry" error={errors.expiry}>
          <input
            id="expiry"
            type="text"
            inputMode="numeric"
            autoComplete="cc-exp"
            value={values.expiry}
            onChange={(e) => handleExpiryChange(e.target.value)}
            className={styles.input}
            placeholder="MM/YY"
          />
        </FormField>

        <FormField label="CVV" htmlFor="cvc" error={errors.cvc}>
          <input
            id="cvc"
            type="password"
            inputMode="numeric"
            autoComplete="cc-csc"
            value={values.cvc}
            onChange={(e) => handleCvcChange(e.target.value)}
            className={styles.input}
            placeholder="123"
          />
        </FormField>
      </div>
    </fieldset>
  )
}
