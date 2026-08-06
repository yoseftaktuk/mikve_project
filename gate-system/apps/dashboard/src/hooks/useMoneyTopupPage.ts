import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../app/api'
import { managementApi } from '../app/managementApi'
import { formatMoney } from '../app/money'
import { useManagementAuth } from './useManagementAuth'
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

/** PIN-protected desk flow: identify by fingerprint, then card top-up. */
export function useMoneyTopupPage() {
  const { authenticated, pin, setPin, pinError, pinLoading, onPinSubmit, logout } = useManagementAuth()

  const [phase, setPhase] = useState<MoneyTopupPhase>('waiting')
  const [user, setUser] = useState<IdentifiedUser | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showCardDialog, setShowCardDialog] = useState(false)
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
      await managementApi.post('/access/management/fingerprint/identify/start')
      setIdentifyReady(true)
      setError(null)
    } catch {
      setIdentifyReady(false)
      setError('לא ניתן להפעיל זיהוי טביעת אצבע. נסו שוב.')
    }
  }, [])

  const cancelIdentify = useCallback(async () => {
    try {
      await managementApi.post('/access/management/fingerprint/identify/cancel')
    } catch {
      // Best-effort; entrance mode resumes when the session TTL expires.
    } finally {
      setIdentifyReady(false)
    }
  }, [])

  useEffect(() => {
    if (!authenticated) return

    let cancelled = false
    const activate = async () => {
      try {
        await managementApi.post('/access/management/fingerprint/identify/start')
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
      void managementApi.post('/access/management/fingerprint/identify/cancel').catch(() => {})
    }
  }, [authenticated])

  useEffect(() => {
    if (!authenticated) return

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
        })
        setPhase('identified')
        setError(null)
        setSimError(null)
        setShowCardDialog(false)
        return
      }

      if (event.type === 'fingerprint.identify_failed') {
        const failed = event as FingerprintIdentifyFailedEvent
        const reason = failed.reason ?? 'unmatched'
        setPhase('failed')
        setUser(null)
        setShowCardDialog(false)
        setError(IDENTIFY_FAIL_MESSAGES[reason] ?? 'הזיהוי נכשל. נסו שוב.')
      }
    }
    return () => ws.close()
  }, [authenticated, wsUrl])

  const refreshBalance = useCallback(async (uid: string) => {
    try {
      const res = await managementApi.get<{
        uid: string
        balance_cents: number
        holder_name?: string | null
        chip_id?: string
      }>(`/access/management/chip/${encodeURIComponent(uid)}`)
      setUser((current) =>
        current && current.uid === uid
          ? {
              ...current,
              balanceCents: res.data.balance_cents,
              holderName: res.data.holder_name ?? current.holderName,
              chipId: res.data.chip_id ?? current.chipId,
            }
          : current,
      )
    } catch {
      // Keep the last known balance if refresh fails.
    }
  }, [])

  const openCardTopup = useCallback(() => {
    if (!user) return
    setShowCardDialog(true)
  }, [user])

  const closeCardTopup = useCallback(() => {
    setShowCardDialog(false)
  }, [])

  const onPaid = useCallback(
    (balanceAfterCents: number) => {
      setShowCardDialog(false)
      setUser((current) => {
        if (!current) return current
        const next = { ...current, balanceCents: balanceAfterCents }
        void refreshBalance(current.uid)
        return next
      })
    },
    [refreshBalance],
  )

  const scanAnother = useCallback(() => {
    setPhase('waiting')
    setUser(null)
    setError(null)
    setSimError(null)
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

  const handleLogout = useCallback(() => {
    void cancelIdentify()
    setPhase('waiting')
    setUser(null)
    setError(null)
    setSimError(null)
    setShowCardDialog(false)
    logout()
  }, [cancelIdentify, logout])

  return {
    authenticated,
    pin,
    setPin,
    pinError,
    pinLoading,
    onPinSubmit,
    logout: handleLogout,
    phase,
    user,
    error,
    identifyReady,
    showCardDialog,
    openCardTopup,
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
