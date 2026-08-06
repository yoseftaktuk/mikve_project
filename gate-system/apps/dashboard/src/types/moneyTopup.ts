export type MoneyTopupPhase = 'waiting' | 'identified' | 'failed'

export type IdentifiedUser = {
  uid: string
  chipId: string
  holderName: string | null
  balanceCents: number
  slot: number | null
}

export type FingerprintIdentifiedEvent = {
  type: 'fingerprint.identified'
  uid: string
  chip_id: string
  holder_name?: string | null
  balance_cents: number
  slot?: number
}

export type FingerprintIdentifyFailedEvent = {
  type: 'fingerprint.identify_failed'
  reason?: string
  slot?: number | null
}
