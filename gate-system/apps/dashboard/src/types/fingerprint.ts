export type PendingApproval = {
  approvalId: string
  uid: string
  holderName: string | null
  balanceCents: number
  feeCents: number
  expiresInSeconds: number
}

/** Enrollment steps: the first three come from the sensor, the rest are terminal. */
export type EnrollStep =
  | 'idle'
  | 'starting'
  | 'place_finger'
  | 'remove_finger'
  | 'place_again'
  | 'stored'
  | 'registered'
  | 'duplicate'
  | 'mismatch'
  | 'timeout'
  | 'cancelled'
  | 'failed'

export type EnrollState = {
  step: EnrollStep
  sessionId: string | null
  holderName: string | null
  slot: number | null
  balanceCents: number | null
}

export type EnrollStartResponse = {
  session_id: string
  holder_name: string
  initial_amount_cents: number
}
