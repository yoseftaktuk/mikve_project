import type { NedarimTransactionResponse } from './topup'

/** postMessage envelope from the Nedarim Plus iframe (docs v=91). */
export type NedarimIframeEvent =
  | { Name: 'Height'; Value: number | string }
  | { Name: 'ValidateFields'; Value: 'OK' | string; Field?: string; ErrorType?: string }
  | { Name: 'TransactionResponse'; Value: NedarimTransactionResponse }

export const NEDARIM_ORIGINS = [
  'https://www.matara.pro',
  'https://matara.pro',
] as const
