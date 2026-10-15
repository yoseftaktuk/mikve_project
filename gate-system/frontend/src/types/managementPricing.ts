export type ManagementPricing = {
  entrance_fee_cents: number
  subscription_price_cents: number
}

export type ManagementPricingUpdate = {
  entrance_fee_cents?: number
  subscription_price_cents?: number
}
