/** Format an amount in cents as an Israeli shekel string. */
export function formatMoney(cents: number) {
  return `₪${(cents / 100).toFixed(2)}`
}

/** Format cents for kiosk displays: whole shekels omit decimals (₪15), otherwise ₪15.50. */
export function formatCompactMoney(cents: number) {
  const shekels = cents / 100
  if (Number.isInteger(shekels)) {
    return `₪${shekels}`
  }
  return `₪${shekels.toFixed(2)}`
}

/** Parse a shekel input ("10", "5,50") into cents, or null when invalid. */
export function parseShekelsToCents(value: string): number | null {
  const normalized = value.trim().replace(',', '.')
  if (!normalized) return null
  const shekels = Number(normalized)
  if (!Number.isFinite(shekels) || shekels <= 0) return null
  return Math.round(shekels * 100)
}

/** Parse a shekel amount that may be zero (e.g. management balance edit). */
export function parseNonNegativeShekelsToCents(value: string): number | null {
  const normalized = value.trim().replace(',', '.')
  if (!normalized) return null
  const shekels = Number(normalized)
  if (!Number.isFinite(shekels) || shekels < 0) return null
  return Math.round(shekels * 100)
}

/** Format cents as a plain shekel number string for form inputs (e.g. "10.00"). */
export function centsToShekelInput(cents: number): string {
  return (cents / 100).toFixed(2)
}
