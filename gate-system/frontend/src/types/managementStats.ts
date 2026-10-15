export type StatisticsRangeKey = 'today' | '7d' | 'month'

export type StatisticsKpis = {
  granted_count: number
  denied_count: number
  approval_rate: number
  entrance_fee_total_cents: number
  paid_card_topup_count: number
  paid_card_topup_total_cents: number
  paid_monthly_subscription_count: number
  paid_monthly_subscription_total_cents: number
  enabled_member_count: number
  disabled_member_count: number
  total_member_balances_cents: number
}

export type DailyAccessPoint = {
  date: string
  granted_count: number
  denied_count: number
  entrance_fee_total_cents: number
}

export type DenialReasonCount = {
  reason: string
  count: number
}

export type AccessMethodsBreakdown = {
  cash: number
  member: number
  subscription: number
}

export type OperationalSummary = {
  pending_card_topups: number
  crediting_card_topups: number
  failed_access_attempts: number
  door_operation_failures: number
}

export type ManagementStatistics = {
  range: StatisticsRangeKey
  timezone: string
  from: string
  to: string
  kpis: StatisticsKpis
  daily_access: DailyAccessPoint[]
  denial_reasons: DenialReasonCount[]
  access_methods: AccessMethodsBreakdown
  operational: OperationalSummary
}
