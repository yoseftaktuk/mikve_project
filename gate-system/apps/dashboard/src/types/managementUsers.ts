export type ManagementUser = {
  chip_id: string
  uid: string
  holder_name: string | null
  is_enabled: boolean
  balance_cents: number
  created_at?: string | null
}

export type ManagementUserUpdate = {
  holder_name?: string | null
  is_enabled?: boolean
}
