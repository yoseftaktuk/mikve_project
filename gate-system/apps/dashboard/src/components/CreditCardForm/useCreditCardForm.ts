import { useCallback } from 'react'
import type { CreditCardFields } from '../../types/chargeChip'

function formatCardNumber(value: string): string {
  const digits = value.replace(/\D/g, '').slice(0, 19)
  return digits.replace(/(\d{4})(?=\d)/g, '$1 ').trim()
}

function formatExpiry(value: string): string {
  const digits = value.replace(/\D/g, '').slice(0, 4)
  if (digits.length <= 2) return digits
  return `${digits.slice(0, 2)}/${digits.slice(2)}`
}

function formatCvc(value: string): string {
  return value.replace(/\D/g, '').slice(0, 4)
}

type UseCreditCardFormParams = {
  onChange: (field: keyof CreditCardFields, value: string) => void
}

/** Formats credit-card input fields as the user types. */
export function useCreditCardForm({ onChange }: UseCreditCardFormParams) {
  const handleCardholderNameChange = useCallback(
    (value: string) => onChange('cardholderName', value),
    [onChange],
  )

  const handleCardNumberChange = useCallback(
    (value: string) => onChange('cardNumber', formatCardNumber(value)),
    [onChange],
  )

  const handleExpiryChange = useCallback(
    (value: string) => onChange('expiry', formatExpiry(value)),
    [onChange],
  )

  const handleCvcChange = useCallback(
    (value: string) => onChange('cvc', formatCvc(value)),
    [onChange],
  )

  return {
    handleCardholderNameChange,
    handleCardNumberChange,
    handleExpiryChange,
    handleCvcChange,
  }
}
