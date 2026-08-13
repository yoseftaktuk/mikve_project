export type MoneyTopupPhase = 'waiting' | 'identified' | 'failed'

export type IdentifiedUser = {
  uid: string
  chipId: string
  holderName: string | null
  balanceCents: number
  slot: number | null
  subscriptionActive: boolean
  subscriptionMonthName: string | null
  currentHebrewMonthName: string | null
}

export type FingerprintIdentifiedEvent = {
  type: 'fingerprint.identified'
  uid: string
  chip_id: string
  holder_name?: string | null
  balance_cents: number
  slot?: number
  subscription_active?: boolean
  subscription_month_name?: string | null
  subscription_free_entry_available_today?: boolean
  current_hebrew_month_name?: string | null
}

export type FingerprintIdentifyFailedEvent = {
  type: 'fingerprint.identify_failed'
  reason?: string
  slot?: number | null
}
