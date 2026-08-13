import axios from 'axios'
import { API_BASE } from './config'
import type {
  CardTopupCreateResponse,
  CardTopupProduct,
  CardTopupSimulatePayResponse,
  CardTopupStatusResponse,
  PaymentHealthResponse,
} from '../types/topup'

/** Axios client for payment-service requests. */
export const paymentsApi = axios.create({
  baseURL: API_BASE,
  timeout: 30_000,
})

/** Open a server-side Nedarim transaction for a chip top-up or subscription. */
export async function createCardTopup(body: {
  fingerprint_uid: string
  amount_cents: number
  product?: CardTopupProduct
}): Promise<CardTopupCreateResponse> {
  const res = await paymentsApi.post<CardTopupCreateResponse>('/payments/card-topups', body)
  return res.data
}

/** Poll server-confirmed top-up status (the only source of truth after payment). */
export async function getCardTopupStatus(topupId: string): Promise<CardTopupStatusResponse> {
  const res = await paymentsApi.get<CardTopupStatusResponse>(`/payments/card-topups/${topupId}`)
  return res.data
}

/** Mark a still-pending top-up abandoned when the user cancels. */
export async function abandonCardTopup(topupId: string): Promise<CardTopupStatusResponse> {
  const res = await paymentsApi.post<CardTopupStatusResponse>(
    `/payments/card-topups/${topupId}/abandon`,
  )
  return res.data
}

/** Preset amounts and payment provider readiness from payment-service. */
export async function getPaymentHealth(): Promise<PaymentHealthResponse> {
  const res = await paymentsApi.get<PaymentHealthResponse>('/payments/healthz')
  return res.data
}

/** Mock mode only: simulate a successful card payment and credit the chip. */
export async function simulateCardTopupPay(topupId: string): Promise<CardTopupSimulatePayResponse> {
  const res = await paymentsApi.post<CardTopupSimulatePayResponse>(
    `/payments/dev/card-topups/${topupId}/simulate-pay`,
  )
  return res.data
}

/** Extract a user-facing error message from an Axios/API failure. */
export function extractApiErrorMessage(error: unknown): string {
  if (!axios.isAxiosError(error)) {
    return 'התשלום נכשל. נסה שוב.'
  }
  const data = error.response?.data as
    | { message?: string; code?: string; detail?: string | { msg: string }[] }
    | undefined
  if (data?.message) return data.message
  if (typeof data?.detail === 'string') return data.detail
  if (Array.isArray(data?.detail) && data.detail[0]?.msg) {
    return data.detail[0].msg
  }
  return 'התשלום נכשל. נסה שוב.'
}
