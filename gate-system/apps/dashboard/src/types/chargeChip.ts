export type CreditCardFields = {
  cardNumber: string
  expiry: string
  cvc: string
  cardholderName: string
}

export type ChargeChipRequest = {
  amount: number
}

export type ChargeChipResponse = {
  message: string
}

export type FieldErrors = {
  amount?: string
  cardNumber?: string
  expiry?: string
  cvc?: string
  cardholderName?: string
}
