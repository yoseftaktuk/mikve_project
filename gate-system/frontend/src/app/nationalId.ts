/** Normalize and validate an Israeli national ID (תעודת זהות). */

/** Return true when id is exactly 9 digits with a valid check digit. */
export function isValidIsraeliId(nationalId: string): boolean {
  if (!/^\d{9}$/.test(nationalId)) return false
  let total = 0
  for (let i = 0; i < 9; i++) {
    const num = Number(nationalId[i]) * ((i % 2) + 1)
    total += num < 10 ? num : num - 9
  }
  return total % 10 === 0
}

/**
 * Normalize user input to a 9-digit Israeli ID.
 * Returns null when the value is empty or invalid.
 */
export function normalizeNationalId(raw: string): string | null {
  const digits = raw.trim()
  if (!digits || !/^\d{1,9}$/.test(digits)) return null
  const padded = digits.padStart(9, '0')
  return isValidIsraeliId(padded) ? padded : null
}
