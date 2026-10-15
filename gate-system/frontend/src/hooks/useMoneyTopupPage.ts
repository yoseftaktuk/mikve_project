import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../app/api'
import { formatMoney } from '../app/money'
import { getPaymentHealth } from '../app/paymentsApi'
import type {
  FingerprintIdentifiedEvent,
  FingerprintIdentifyFailedEvent,
  IdentifiedUser,
  MoneyTopupPhase,
} from '../types/moneyTopup'

const IDENTIFY_FAIL_MESSAGES: Record<string, string> = {
  unmatched: 'האצבע לא מזוהה במערכת. יש לרשום אותה קודם.',
  unknown_fingerprint: 'לא נמצא כרטיס עבור טביעת האצבע הזו.',
  chip_disabled: 'הכרטיס של משתמש זה מושבת.',
}

/** Desk flow: identify by fingerprint, then card top-up or subscription purchase. */
export function useMoneyTopupPage() {
  const [phase, setPhase] = useState<MoneyTopupPhase>('waiting')
  const [user, setUser] = useState<IdentifiedUser | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showCardDialog, setShowCardDialog] = useState(false)
  const [cardDialogProduct, setCardDialogProduct] = useState<'balance' | 'monthly_subscription'>('balance')
  const [hebrewMonthName, setHebrewMonthName] = useState<string | null>(null)
  const [subscriptionPriceCents, setSubscriptionPriceCents] = useState(30000)
  const [lastTopupSuccess, setLastTopupSuccess] = useState<string | null>(null)
  const [identifyReady, setIdentifyReady] = useState(false)
  const [simSlot, setSimSlot] = useState('1')
  const [simLoading, setSimLoading] = useState(false)
  const [simError, setSimError] = useState<string | null>(null)

  const wsUrl = useMemo(() => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    return `${proto}://${location.host}/api/access/ws/events`
  }, [])

  const startIdentify = useCallback(async () => {
    try {
      await api.post('/access/management/fingerprint/identify/start')
      setIdentifyReady(true)
      setError(null)
    } catch {
      setIdentifyReady(false)
      setError('לא ניתן להפעיל זיהוי טביעת אצבע. נסו שוב.')
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    getPaymentHealth()
      .then((health) => {
        if (cancelled) return
        if (health.current_hebrew_month_name) {
          setHebrewMonthName(health.current_hebrew_month_name)
        }
        if (
          typeof health.subscription_price_cents === 'number' &&
          health.subscription_price_cents > 0
        ) {
          setSubscriptionPriceCents(health.subscription_price_cents)
        }
      })
      .catch(() => {
        // Month label falls back to the identify payload.
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    const activate = async () => {
      try {
        await api.post('/access/management/fingerprint/identify/start')
        if (cancelled) return
        setIdentifyReady(true)
        setError(null)
      } catch {
        if (cancelled) return
        setIdentifyReady(false)
        setError('לא ניתן להפעיל זיהוי טביעת אצבע. נסו שוב.')
      }
    }

    void activate()
    const refresh = window.setInterval(() => {
      void activate()
    }, 120_000)

    return () => {
      cancelled = true
      window.clearInterval(refresh)
      void api.post('/access/management/fingerprint/identify/cancel').catch(() => {})
    }
  }, [])

  useEffect(() => {
    const ws = new WebSocket(wsUrl)
    ws.onmessage = (msg) => {
      let event: { type?: string }
      try {
        event = JSON.parse(msg.data)
      } catch {
        return
      }

      if (event.type === 'fingerprint.identified') {
        const identified = event as FingerprintIdentifiedEvent
        setUser({
          uid: identified.uid,
          chipId: identified.chip_id,
          holderName: identified.holder_name ?? null,
          balanceCents: identified.balance_cents,
          slot: identified.slot ?? null,
          subscriptionActive: Boolean(identified.subscription_active),
          subscriptionMonthName: identified.subscription_month_name ?? null,
          currentHebrewMonthName: identified.current_hebrew_month_name ?? null,
        })
        if (identified.current_hebrew_month_name) {
          setHebrewMonthName(identified.current_hebrew_month_name)
        }
        setPhase('identified')
        setError(null)
        setSimError(null)
        setLastTopupSuccess(null)
        setShowCardDialog(false)
        return
      }

      if (event.type === 'fingerprint.identify_failed') {
        const failed = event as FingerprintIdentifyFailedEvent
        const reason = failed.reason ?? 'unmatched'
        setPhase('failed')
        setUser(null)
        setShowCardDialog(false)
        setLastTopupSuccess(null)
        setError(IDENTIFY_FAIL_MESSAGES[reason] ?? 'הזיהוי נכשל. נסו שוב.')
      }
    }
    return () => ws.close()
  }, [wsUrl])

  const refreshBalance = useCallback(async (uid: string) => {
    try {
      const res = await api.get<{
        uid: string
        balance_cents: number
        holder_name?: string | null
        chip_id?: string
        subscription_active?: boolean
        subscription_month_name?: string | null
        current_hebrew_month_name?: string | null
      }>(`/access/management/chip/${encodeURIComponent(uid)}`)
      const data = res.data
      if (data.current_hebrew_month_name) {
        setHebrewMonthName(data.current_hebrew_month_name)
      }
      setUser((current) =>
        current && current.uid === uid
          ? {
              ...current,
              balanceCents: data.balance_cents,
              holderName: data.holder_name ?? current.holderName,
              chipId: data.chip_id ?? current.chipId,
              subscriptionActive:
                typeof data.subscription_active === 'boolean'
                  ? data.subscription_active
                  : current.subscriptionActive,
              subscriptionMonthName: data.subscription_month_name ?? current.subscriptionMonthName,
              currentHebrewMonthName:
                data.current_hebrew_month_name ?? current.currentHebrewMonthName,
            }
          : current,
      )
    } catch {
      // Keep the last known balance if refresh fails.
    }
  }, [])

  const openCardTopup = useCallback(() => {
    if (!user) return
    setLastTopupSuccess(null)
    setCardDialogProduct('balance')
    setShowCardDialog(true)
  }, [user])

  const openSubscriptionPurchase = useCallback(() => {
    if (!user || user.subscriptionActive) return
    setLastTopupSuccess(null)
    setCardDialogProduct('monthly_subscription')
    setShowCardDialog(true)
  }, [user])

  const closeCardTopup = useCallback(() => {
    setShowCardDialog(false)
  }, [])

  const onPaid = useCallback(
    (balanceAfterCents: number) => {
      if (cardDialogProduct === 'monthly_subscription') {
        const month = hebrewMonthName || user?.currentHebrewMonthName || 'החודש הנוכחי'
        setLastTopupSuccess(`מנוי חודשי לחודש ${month} הופעל בהצלחה`)
        setUser((current) =>
          current
            ? {
                ...current,
                subscriptionActive: true,
                subscriptionMonthName: month,
                currentHebrewMonthName: month,
              }
            : current,
        )
        if (user) void refreshBalance(user.uid)
        return
      }
      setLastTopupSuccess(`היתרה עודכנה בהצלחה — ${formatMoney(balanceAfterCents)}`)
      setUser((current) => {
        if (!current) return current
        const next = { ...current, balanceCents: balanceAfterCents }
        void refreshBalance(current.uid)
        return next
      })
    },
    [cardDialogProduct, hebrewMonthName, refreshBalance, user],
  )

  const scanAnother = useCallback(() => {
    setPhase('waiting')
    setUser(null)
    setError(null)
    setSimError(null)
    setLastTopupSuccess(null)
    setShowCardDialog(false)
    void startIdentify()
  }, [startIdentify])

  const simulateFingerprint = useCallback(async (slot: number | null) => {
    setSimLoading(true)
    setSimError(null)
    try {
      await api.post('/hardware/dev/fingerprint/scan', { slot })
    } catch {
      setSimError('סימולציית האצבע נכשלה. ודא ש-HARDWARE_MODE=mock והשרתים רצים.')
    } finally {
      setSimLoading(false)
    }
  }, [])

  const simulateSlotFromInput = useCallback(async () => {
    const parsed = Number.parseInt(simSlot.trim(), 10)
    if (!Number.isFinite(parsed) || parsed < 0 || parsed > 1000) {
      setSimError('הזן מספר מזהה טביעה תקין (0–1000).')
      return
    }
    await simulateFingerprint(parsed)
  }, [simSlot, simulateFingerprint])

  const simulateUnmatched = useCallback(() => {
    void simulateFingerprint(null)
  }, [simulateFingerprint])

  return {
    phase,
    user,
    error,
    lastTopupSuccess,
    identifyReady,
    showCardDialog,
    cardDialogProduct,
    hebrewMonthName,
    subscriptionPriceCents,
    openCardTopup,
    openSubscriptionPurchase,
    closeCardTopup,
    onPaid,
    scanAnother,
    simSlot,
    setSimSlot,
    simLoading,
    simError,
    simulateSlotFromInput,
    simulateUnmatched,
    formatMoney,
  }
}
