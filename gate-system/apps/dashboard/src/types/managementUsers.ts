export type ManagementUser = {
  fingerprint_id: string
  uid: string
  holder_name: string | null
  national_id?: string | null
  is_enabled: boolean
  balance_cents: number
  created_at?: string | null
}

export type ManagementUserUpdate = {
  holder_name?: string | null
  national_id?: string | null
  is_enabled?: boolean
  balance_cents?: number
}
