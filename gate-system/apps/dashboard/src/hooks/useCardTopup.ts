import { useCallback, useEffect, useRef, useState } from 'react'
import {
  abandonCardTopup,
  createCardTopup,
  extractApiErrorMessage,
  getCardTopupStatus,
  getPaymentHealth,
  simulateCardTopupPay,
} from '../app/paymentsApi'
import type { CardTopupCreateResponse, CardTopupStatusResponse } from '../types/topup'

const DEFAULT_AMOUNTS = [2000, 5000, 10000]
const POLL_MS = 2000
const POLL_TIMEOUT_MS = 120_000

export type CardTopupPhase =
  | 'choose_amount'
  | 'creating'
  | 'ready'
  | 'submitting'
  | 'waiting_server'
  | 'paid'
  | 'failed'

/** Creates a Nedarim top-up and polls until the server-side callback credits it. */
export function useCardTopup(chipUid: string | null) {
  const [phase, setPhase] = useState<CardTopupPhase>('choose_amount')
  const [amountsCents, setAmountsCents] = useState<number[]>(DEFAULT_AMOUNTS)
  const [paymentMode, setPaymentMode] = useState<'mock' | 'nedarim'>('nedarim')
  const [created, setCreated] = useState<CardTopupCreateResponse | null>(null)
  const [status, setStatus] = useState<CardTopupStatusResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [clientMessage, setClientMessage] = useState<string | null>(null)
  const pollStartedAt = useRef<number | null>(null)

  useEffect(() => {
    let cancelled = false
    getPaymentHealth()
      .then((health) => {
        if (cancelled) return
        if (health.topup_amounts_cents?.length) {
          setAmountsCents(health.topup_amounts_cents)
        }
        if (health.payment_mode === 'mock' || health.payment_mode === 'nedarim') {
          setPaymentMode(health.payment_mode)
        }
      })
      .catch(() => {
        // Keep the documented defaults when payment-service is unreachable.
      })
    return () => {
      cancelled = true
    }
  }, [])

  const reset = useCallback(() => {
    setPhase('choose_amount')
    setCreated(null)
    setStatus(null)
    setError(null)
    setClientMessage(null)
    pollStartedAt.current = null
  }, [])

  const startTopup = useCallback(
    async (amountCents: number) => {
      if (!chipUid) return
      setPhase('creating')
      setError(null)
      setClientMessage(null)
      try {
        const result = await createCardTopup({ chip_uid: chipUid, amount_cents: amountCents })
        setCreated(result)
        setPhase('ready')
      } catch (err) {
        setError(extractApiErrorMessage(err))
        setPhase('failed')
      }
    },
    [chipUid],
  )

  const beginPolling = useCallback(() => {
    setPhase('waiting_server')
    pollStartedAt.current = Date.now()
  }, [])

  const onClientTransactionResponse = useCallback(
    (ok: boolean, message?: string) => {
      if (!ok) {
        setClientMessage(message || 'העסקה נדחתה. נסה שוב או בחר סכום אחר.')
        setPhase('ready')
        return
      }
      setClientMessage('התשלום התקבל — מאשרים מול השרת…')
      beginPolling()
    },
    [beginPolling],
  )

  useEffect(() => {
    if (phase !== 'waiting_server' || !created) return

    let cancelled = false
    let timer: number | null = null

    const tick = async () => {
      if (cancelled || !created) return
      if (pollStartedAt.current && Date.now() - pollStartedAt.current > POLL_TIMEOUT_MS) {
        setError(
          'התשלום עשוי להתקבל, אך האישור מהשרת עדיין לא הגיע. פנה לדלפק לבדיקת היתרה.',
        )
        setPhase('failed')
        return
      }
      try {
        const next = await getCardTopupStatus(created.topup_id)
        if (cancelled) return
        setStatus(next)
        if (next.status === 'paid') {
          setPhase('paid')
          return
        }
        if (next.status === 'failed' || next.status === 'abandoned') {
          setError(next.error_code || 'הטעינה נכשלה')
          setPhase('failed')
          return
        }
      } catch {
        // Transient errors — keep polling until timeout.
      }
      timer = window.setTimeout(() => void tick(), POLL_MS)
    }

    void tick()
    return () => {
      cancelled = true
      if (timer != null) window.clearTimeout(timer)
    }
  }, [phase, created])

  const cancelPendingTopup = useCallback(async () => {
    if (created && (phase === 'ready' || phase === 'submitting' || phase === 'waiting_server')) {
      try {
        await abandonCardTopup(created.topup_id)
      } catch {
        // Abandon is best-effort; the row expires from the user's perspective either way.
      }
    }
    reset()
  }, [created, phase, reset])

  const markSubmitting = useCallback(() => setPhase('submitting'), [])

  return {
    phase,
    paymentMode,
    amountsCents,
    created,
    status,
    error,
    clientMessage,
    startTopup,
    onClientTransactionResponse,
    cancelPendingTopup,
    markSubmitting,
    reset,
    setPhase,
    setError,
    simulateMockPay: simulateCardTopupPay,
  }
}
