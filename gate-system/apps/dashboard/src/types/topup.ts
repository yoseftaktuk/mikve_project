/** Fingerprint identified a person who needs to top up before entrance. */
export type TopupOffer = {
  uid: string
  chipId: string
  holderName: string | null
  balanceCents: number
  feeCents: number
}

export type CardTopupCreateResponse = {
  topup_id: string
  nedarim_transaction_id: string
  iframe_url: string
  amount_cents: number
  chip_uid: string
  chip_id: string
}

export type CardTopupStatusResponse = {
  topup_id: string
  status: 'pending' | 'crediting' | 'paid' | 'failed' | 'abandoned' | string
  amount_cents: number
  chip_uid: string
  chip_id: string
  nedarim_transaction_id?: string | null
  balance_after_cents?: number | null
  last_num?: string | null
  error_code?: string | null
}

export type PaymentHealthResponse = {
  status: string
  payment_mode?: 'mock' | 'nedarim'
  topup_amounts_cents: number[]
  nedarim_configured?: boolean
  public_base_url_set?: boolean
}

export type CardTopupSimulatePayResponse = {
  status: string
  code: string
  message: string
  balance_after_cents?: number | null
}

export type NedarimTransactionResponse = {
  Status: string
  Message?: string
  ID?: string
  Confirmation?: string
  LastNum?: string
}
