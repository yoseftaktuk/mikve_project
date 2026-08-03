/** Format an amount in cents as an Israeli shekel string. */
export function formatMoney(cents: number) {
  return `₪${(cents / 100).toFixed(2)}`
}

/** Parse a shekel input ("10", "5,50") into cents, or null when invalid. */
export function parseShekelsToCents(value: string): number | null {
  const normalized = value.trim().replace(',', '.')
  if (!normalized) return null
  const shekels = Number(normalized)
  if (!Number.isFinite(shekels) || shekels <= 0) return null
  return Math.round(shekels * 100)
}
