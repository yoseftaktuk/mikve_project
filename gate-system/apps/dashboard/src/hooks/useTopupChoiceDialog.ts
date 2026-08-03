import type { TopupOffer } from '../types/topup'

type UseTopupChoiceDialogParams = {
  offer: TopupOffer
}

/** Labels for the insufficient-balance top-up chooser. */
export function useTopupChoiceDialog({ offer }: UseTopupChoiceDialogParams) {
  const displayName = offer.holderName?.trim() || 'ללא שם'
  const shortfallCents = Math.max(0, offer.feeCents - offer.balanceCents)
  return { displayName, shortfallCents }
}
