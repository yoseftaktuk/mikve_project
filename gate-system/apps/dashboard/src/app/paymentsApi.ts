import axios from 'axios'
import { API_BASE } from './config'
import type { ChargeChipRequest, ChargeChipResponse } from '../types/chargeChip'

export const paymentsApi = axios.create({
  baseURL: API_BASE,
  timeout: 30_000,
})

export async function chargeChip(body: ChargeChipRequest): Promise<ChargeChipResponse> {
  const res = await paymentsApi.post<ChargeChipResponse>('/payments/charge-chip', body)
  return res.data
}

export function extractApiErrorMessage(error: unknown): string {
  if (!axios.isAxiosError(error)) {
    return 'Payment failed. Please try again.'
  }
  const data = error.response?.data as { message?: string; detail?: string | { msg: string }[] } | undefined
  if (data?.message) return data.message
  if (typeof data?.detail === 'string') return data.detail
  if (Array.isArray(data?.detail) && data.detail[0]?.msg) {
    return data.detail[0].msg
  }
  return 'Payment failed. Please try again.'
}
