import type { CreditCardFields, FieldErrors } from '../types/chargeChip'

/** Validate that a top-up amount is a positive number of at least 1. */
export function validateAmount(value: string): string | undefined {
  const trimmed = value.trim()
  if (!trimmed) {
    return 'סכום הוא שדה חובה.'
  }
  const amount = Number(trimmed.replace(',', '.'))
  if (!Number.isFinite(amount)) {
    return 'הזן מספר תקין.'
  }
  if (amount <= 0) {
    return 'הסכום חייב להיות גדול מ-0.'
  }
  if (amount < 1) {
    return 'הסכום המינימלי הוא 1.'
  }
  return undefined
}

function digitsOnly(value: string): string {
  return value.replace(/\D/g, '')
}

function luhnCheck(cardNumber: string): boolean {
  let sum = 0
  let alternate = false
  for (let i = cardNumber.length - 1; i >= 0; i -= 1) {
    let n = Number(cardNumber[i])
    if (alternate) {
      n *= 2
      if (n > 9) n -= 9
    }
    sum += n
    alternate = !alternate
  }
  return sum % 10 === 0
}

function validateExpiry(value: string): string | undefined {
  const match = value.trim().match(/^(\d{2})\s*\/\s*(\d{2})$/)
  if (!match) {
    return 'הזן תוקף בפורמט MM/YY.'
  }
  const month = Number(match[1])
  const year = Number(match[2]) + 2000
  if (month < 1 || month > 12) {
    return 'חודש לא תקין.'
  }
  const now = new Date()
  const expiry = new Date(year, month, 0, 23, 59, 59)
  if (expiry < now) {
    return 'כרטיס האשראי פג תוקף.'
  }
  return undefined
}

/** Validate credit-card fields and return per-field error messages. */
export function validateCreditCard(fields: CreditCardFields): Pick<FieldErrors, 'cardNumber' | 'expiry' | 'cvc' | 'cardholderName'> {
  const errors: Pick<FieldErrors, 'cardNumber' | 'expiry' | 'cvc' | 'cardholderName'> = {}
  const cardDigits = digitsOnly(fields.cardNumber)

  if (!fields.cardholderName.trim()) {
    errors.cardholderName = 'שם בעל הכרטיס הוא שדה חובה.'
  }

  if (!cardDigits) {
    errors.cardNumber = 'מספר כרטיס הוא שדה חובה.'
  } else if (cardDigits.length < 13 || cardDigits.length > 19) {
    errors.cardNumber = 'מספר כרטיס לא תקין.'
  } else if (!luhnCheck(cardDigits)) {
    errors.cardNumber = 'מספר כרטיס לא תקין.'
  }

  const expiryError = validateExpiry(fields.expiry)
  if (expiryError) {
    errors.expiry = expiryError
  }

  const cvcDigits = digitsOnly(fields.cvc)
  if (!cvcDigits) {
    errors.cvc = 'קוד אבטחה (CVV) הוא שדה חובה.'
  } else if (cvcDigits.length < 3 || cvcDigits.length > 4) {
    errors.cvc = 'קוד אבטחה לא תקין.'
  }

  return errors
}

/** Parse a shekel amount string into a number, or null if invalid. */
export function parseAmount(value: string): number | null {
  const trimmed = value.trim().replace(',', '.')
  if (!trimmed) return null
  const amount = Number(trimmed)
  if (!Number.isFinite(amount) || amount < 1) return null
  return amount
}
