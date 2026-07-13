import { useCallback, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { chargeChip, extractApiErrorMessage } from '../../../app/paymentsApi'
import { parseAmount, validateAmount, validateCreditCard } from '../../../app/chargeChipValidation'
import type { CreditCardFields, FieldErrors } from '../../../types/chargeChip'

const EMPTY_CARD: CreditCardFields = {
  cardNumber: '',
  expiry: '',
  cvc: '',
  cardholderName: '',
}

/** Form state and submit flow for charging a chip via credit card. */
export function useChargeChipPage() {
  const navigate = useNavigate()
  const [amount, setAmount] = useState('')
  const [card, setCard] = useState<CreditCardFields>(EMPTY_CARD)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const isFormLocked = loading || Boolean(successMessage)

  const updateCard = useCallback((field: keyof CreditCardFields, value: string) => {
    setCard((prev) => ({ ...prev, [field]: value }))
    setFieldErrors((prev) => ({ ...prev, [field]: undefined }))
  }, [])

  const handleAmountChange = useCallback((value: string) => {
    setAmount(value)
    setFieldErrors((prev) => ({ ...prev, amount: undefined }))
  }, [])

  const onSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      setSubmitError(null)
      setSuccessMessage(null)

      const amountError = validateAmount(amount)
      const cardErrors = validateCreditCard(card)
      const nextErrors: FieldErrors = { ...cardErrors }
      if (amountError) nextErrors.amount = amountError

      if (Object.keys(nextErrors).length > 0) {
        setFieldErrors(nextErrors)
        return
      }

      const parsedAmount = parseAmount(amount)
      if (parsedAmount == null) {
        setFieldErrors({ amount: 'הסכום המינימלי הוא 1.' })
        return
      }

      setLoading(true)
      try {
        const res = await chargeChip({ amount: parsedAmount })
        setSuccessMessage(res.message || 'Chip charged successfully.')
        window.setTimeout(() => navigate('/dashboard'), 1500)
      } catch (err) {
        setSubmitError(extractApiErrorMessage(err))
      } finally {
        setLoading(false)
      }
    },
    [amount, card, navigate],
  )

  return {
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
  }
}
