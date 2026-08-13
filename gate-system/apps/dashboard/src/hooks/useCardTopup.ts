import { useCallback, useEffect, useRef, useState } from 'react'
import {
  abandonCardTopup,
  createCardTopup,
  extractApiErrorMessage,
  getCardTopupStatus,
  getPaymentHealth,
  simulateCardTopupPay,
} from '../app/paymentsApi'
import type {
  CardTopupCreateResponse,
  CardTopupProduct,
  CardTopupStatusResponse,
} from '../types/topup'

const DEFAULT_AMOUNTS = [2000, 5000, 10000]
const DEFAULT_SUBSCRIPTION_CENTS = 30000
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

type UseCardTopupOptions = {
  product?: CardTopupProduct
}

/** Creates a Nedarim top-up/subscription and polls until the server-side callback settles it. */
export function useCardTopup(fingerprintUid: string | null, options: UseCardTopupOptions = {}) {
  const product: CardTopupProduct = options.product ?? 'balance'
  const [phase, setPhase] = useState<CardTopupPhase>(
    product === 'monthly_subscription' ? 'creating' : 'choose_amount',
  )
  const [amountsCents, setAmountsCents] = useState<number[]>(DEFAULT_AMOUNTS)
  const [subscriptionPriceCents, setSubscriptionPriceCents] = useState(DEFAULT_SUBSCRIPTION_CENTS)
  const [hebrewMonthName, setHebrewMonthName] = useState<string | null>(null)
  const [paymentMode, setPaymentMode] = useState<'mock' | 'nedarim'>('nedarim')
  const [created, setCreated] = useState<CardTopupCreateResponse | null>(null)
  const [status, setStatus] = useState<CardTopupStatusResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [clientMessage, setClientMessage] = useState<string | null>(null)
  const pollStartedAt = useRef<number | null>(null)
  const autoStartedRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    getPaymentHealth()
      .then((health) => {
        if (cancelled) return
        if (health.topup_amounts_cents?.length) {
          setAmountsCents(health.topup_amounts_cents)
        }
        if (typeof health.subscription_price_cents === 'number' && health.subscription_price_cents > 0) {
          setSubscriptionPriceCents(health.subscription_price_cents)
        }
        if (health.current_hebrew_month_name) {
          setHebrewMonthName(health.current_hebrew_month_name)
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
    setPhase(product === 'monthly_subscription' ? 'creating' : 'choose_amount')
    setCreated(null)
    setStatus(null)
    setError(null)
    setClientMessage(null)
    pollStartedAt.current = null
    autoStartedRef.current = false
  }, [product])

  const startTopup = useCallback(
    async (amountCents: number, purchaseProduct: CardTopupProduct = product) => {
      // #region agent log
      fetch('http://127.0.0.1:7292/ingest/63c6dbc4-c680-4396-a7ce-14fb5d793358',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'833d12'},body:JSON.stringify({sessionId:'833d12',runId:'pre-fix',hypothesisId:'B',location:'useCardTopup.ts:startTopup',message:'startTopup called',data:{amountCents,purchaseProduct,fingerprintUid:Boolean(fingerprintUid)},timestamp:Date.now()})}).catch(()=>{});
      // #endregion
      if (!fingerprintUid) return
      setPhase('creating')
      setError(null)
      setClientMessage(null)
      try {
        const result = await createCardTopup({
          fingerprint_uid: fingerprintUid,
          amount_cents: amountCents,
          product: purchaseProduct,
        })
        // #region agent log
        fetch('http://127.0.0.1:7292/ingest/63c6dbc4-c680-4396-a7ce-14fb5d793358',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'833d12'},body:JSON.stringify({sessionId:'833d12',runId:'pre-fix',hypothesisId:'D',location:'useCardTopup.ts:startTopup:ok',message:'createCardTopup succeeded',data:{topupId:result.topup_id,product:result.product,amount:result.amount_cents},timestamp:Date.now()})}).catch(()=>{});
        // #endregion
        setCreated(result)
        setPhase('ready')
      } catch (err) {
        // #region agent log
        fetch('http://127.0.0.1:7292/ingest/63c6dbc4-c680-4396-a7ce-14fb5d793358',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'833d12'},body:JSON.stringify({sessionId:'833d12',runId:'pre-fix',hypothesisId:'C',location:'useCardTopup.ts:startTopup:err',message:'createCardTopup failed',data:{error:extractApiErrorMessage(err)},timestamp:Date.now()})}).catch(()=>{});
        // #endregion
        setError(extractApiErrorMessage(err))
        setPhase('failed')
      }
    },
    [fingerprintUid, product],
  )

  useEffect(() => {
    const skipReason =
      product !== 'monthly_subscription'
        ? 'not_subscription'
        : !fingerprintUid
          ? 'no_uid'
          : autoStartedRef.current
            ? 'already_auto_started'
            : phase !== 'creating'
              ? 'phase_not_creating'
              : created
                ? 'already_created'
                : null
    // #region agent log
    fetch('http://127.0.0.1:7292/ingest/63c6dbc4-c680-4396-a7ce-14fb5d793358',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'833d12'},body:JSON.stringify({sessionId:'833d12',runId:'post-fix',hypothesisId:'A',location:'useCardTopup.ts:autoStartEffect',message:'auto-start effect run',data:{product,phase,hasUid:Boolean(fingerprintUid),autoStarted:autoStartedRef.current,hasCreated:Boolean(created),subscriptionPriceCents,skipReason},timestamp:Date.now()})}).catch(()=>{});
    // #endregion
    if (skipReason) return
    // Mark started only when the deferred create actually runs. Strict Mode
    // remounts clear the timer; setting the flag earlier permanently skipped create.
    let cancelled = false
    const timer = window.setTimeout(() => {
      if (cancelled || autoStartedRef.current) return
      autoStartedRef.current = true
      // #region agent log
      fetch('http://127.0.0.1:7292/ingest/63c6dbc4-c680-4396-a7ce-14fb5d793358',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'833d12'},body:JSON.stringify({sessionId:'833d12',runId:'post-fix',hypothesisId:'A',location:'useCardTopup.ts:autoStartTimeout',message:'auto-start timeout fired',data:{subscriptionPriceCents},timestamp:Date.now()})}).catch(()=>{});
      // #endregion
      void startTopup(subscriptionPriceCents, 'monthly_subscription')
    }, 0)
    return () => {
      cancelled = true
      // #region agent log
      fetch('http://127.0.0.1:7292/ingest/63c6dbc4-c680-4396-a7ce-14fb5d793358',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'833d12'},body:JSON.stringify({sessionId:'833d12',runId:'post-fix',hypothesisId:'A',location:'useCardTopup.ts:autoStartCleanup',message:'auto-start effect cleanup cleared timer',data:{autoStarted:autoStartedRef.current},timestamp:Date.now()})}).catch(()=>{});
      // #endregion
      window.clearTimeout(timer)
    }
  }, [product, fingerprintUid, phase, created, startTopup, subscriptionPriceCents])

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
    product,
    paymentMode,
    amountsCents,
    subscriptionPriceCents,
    hebrewMonthName,
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
