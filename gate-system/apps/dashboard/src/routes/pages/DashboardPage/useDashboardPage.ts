import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../../app/api'
import type { ChipToastData } from '../../../components/ChipToast/types'
import type { GateStatus } from '../../../components/GateEntrancePanel'

type WsEvent = {
  type?: string
  method?: string
  uid?: string | null
  reason?: string
  balance_cents?: number
  balance_after_cents?: number
  fee_cents?: number
  amount_cents?: number
  total_cents?: number
  required_cents?: number
  remaining_cents?: number
  previous_total_cents?: number
  timeout_seconds?: number
}

type AccessDecision = {
  granted: boolean
  reason: string
  fee_cents: number
  balance_before_cents?: number | null
  balance_after_cents?: number | null
}

type SimulateCashResult = {
  granted: boolean
  accumulated_cents: number
  entrance_fee_cents: number
  remaining_cents: number
}

export function formatMoney(cents: number) {
  return `₪${(cents / 100).toFixed(2)}`
}

function grantedToast(event: { balance_after_cents?: number; remaining_cents?: number; method?: string }): ChipToastData {
  const isCash = event.method === 'cash'
  const changeCents = event.remaining_cents ?? 0
  return {
    kind: 'granted',
    title: 'הדלת נפתחה',
    message: isCash ? 'תשלום התקבל בהצלחה. ברוך הבא!' : 'ניכוי עלות כניסה בוצע בהצלחה. ברוך הבא!',
    balanceCents: isCash
      ? changeCents > 0
        ? changeCents
        : null
      : (event.balance_after_cents ?? null),
    balanceLabel: isCash ? 'עודף' : "יתרה נותרת בצ'יפ",
  }
}

function isCashGrantedEvent(event: WsEvent): boolean {
  return (
    event.type === 'access.granted' &&
    (event.method === 'cash' || event.reason === 'cash_paid')
  )
}

function chipToastFromEvent(event: WsEvent): ChipToastData | null {
  if (isCashGrantedEvent(event)) {
    return grantedToast({ method: 'cash', remaining_cents: event.remaining_cents })
  }

  if (event.type === 'access.granted' && event.uid) {
    return grantedToast(event)
  }

  if (event.type === 'access.denied' && event.uid != null) {
    const balance = event.balance_cents ?? null
    if (event.reason === 'insufficient_balance') {
      const fee = event.fee_cents
      return {
        kind: 'denied',
        title: 'אין מספיק יתרה',
        message:
          fee != null
            ? `נדרשים ${formatMoney(fee)} לכניסה. אנא טען את הצ'יפ או שלם במזומן.`
            : "אין מספיק יתרה בצ'יפ. אנא טען או שלם במזומן.",
        balanceCents: balance,
      }
    }
    if (event.reason === 'chip_disabled') {
      return {
        kind: 'denied',
        title: "צ'יפ חסום",
        message: "הצ'יפ הזה אינו פעיל. פנה למנהל המערכת.",
        balanceCents: balance,
      }
    }
    if (event.reason === 'unknown_chip') {
      return {
        kind: 'denied',
        title: "צ'יפ לא מזוהה",
        message: "הצ'יפ לא רשום במערכת.",
        balanceCents: null,
      }
    }
  }

  return null
}

function chipToastFromDecision(decision: AccessDecision): ChipToastData {
  if (decision.granted) {
    return grantedToast({ balance_after_cents: decision.balance_after_cents ?? undefined })
  }
  if (decision.reason === 'insufficient_balance') {
    return {
      kind: 'denied',
      title: 'אין מספיק יתרה',
      message: `נדרשים ${formatMoney(decision.fee_cents)} לכניסה.`,
      balanceCents: decision.balance_before_cents ?? null,
    }
  }
  return {
    kind: 'denied',
    title: 'הכניסה נדחתה',
    message: decision.reason,
    balanceCents: decision.balance_before_cents ?? null,
  }
}

export function useDashboardPage() {
  const [gateStatus, setGateStatus] = useState<GateStatus | null>(null)
  const [chipToast, setChipToast] = useState<ChipToastData | null>(null)
  const [lastActivity, setLastActivity] = useState<string | null>(null)
  const [simError, setSimError] = useState<string | null>(null)
  const [simLoading, setSimLoading] = useState(false)
  const toastTimer = useRef<number | null>(null)

  const wsUrl = useMemo(() => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    return `${proto}://${location.host}/api/access/ws/events`
  }, [])

  const cashProgress = gateStatus
    ? Math.min(100, (gateStatus.cash_accumulated_cents / gateStatus.entrance_fee_cents) * 100)
    : 0

  const refreshStatus = useCallback(() => {
    api.get<GateStatus>('/access/healthz').then((r) => setGateStatus(r.data)).catch(() => {})
  }, [])

  const showChipToast = useCallback((toast: ChipToastData) => {
    if (toastTimer.current != null) window.clearTimeout(toastTimer.current)
    setChipToast(toast)
    if (toast.kind === 'granted') {
      toastTimer.current = window.setTimeout(() => setChipToast(null), 4000)
    }
  }, [])

  const dismissChipToast = useCallback(() => setChipToast(null), [])

  const simulateChip = useCallback(async () => {
    setSimLoading(true)
    setSimError(null)
    try {
      const res = await api.post<AccessDecision>('/access/dev/simulate/chip')
      showChipToast(chipToastFromDecision(res.data))
      refreshStatus()
      setLastActivity(res.data.granted ? "סימולציית צ'יפ — הדלת נפתחה" : "סימולציית צ'יפ — הכניסה נדחתה")
    } catch {
      setSimError("סימולציית הצ'יפ נכשלה. ודא שהשרתים רצים (docker compose up).")
    } finally {
      setSimLoading(false)
    }
  }, [refreshStatus, showChipToast])

  const simulateCash = useCallback(
    async (amountCents: number) => {
      setSimLoading(true)
      setSimError(null)
      try {
        const res = await api.post<SimulateCashResult>('/access/dev/simulate/cash', { amount_cents: amountCents })
        refreshStatus()
        if (res.data.granted) {
          showChipToast(
            grantedToast({
              method: 'cash',
              remaining_cents: res.data.remaining_cents,
            }),
          )
          setLastActivity(`סימולציית מזומן — הדלת נפתחה (${formatMoney(amountCents)} הוכנסו)`)
        } else {
          setLastActivity(
            `הוכנס ${formatMoney(amountCents)} — סה"כ ${formatMoney(res.data.accumulated_cents)} מתוך ${formatMoney(res.data.entrance_fee_cents)}`,
          )
        }
      } catch {
        setSimError('סימולציית המזומן נכשלה. ודא שהשרתים רצים (docker compose up).')
      } finally {
        setSimLoading(false)
      }
    },
    [refreshStatus, showChipToast],
  )

  useEffect(() => {
    refreshStatus()
    const interval = window.setInterval(refreshStatus, 10000)
    return () => window.clearInterval(interval)
  }, [refreshStatus])

  useEffect(() => {
    const ws = new WebSocket(wsUrl)
    ws.onmessage = (msg) => {
      let event: WsEvent
      try {
        event = JSON.parse(msg.data)
      } catch {
        return
      }

      refreshStatus()

      const toast = chipToastFromEvent(event)
      if (toast) {
        showChipToast(toast)
      }

      if (event.type === 'cash.accumulated' && event.total_cents != null && event.required_cents != null) {
        setLastActivity(`הוכנס ${formatMoney(event.amount_cents ?? 0)} — סה"כ ${formatMoney(event.total_cents)} מתוך ${formatMoney(event.required_cents)}`)
      } else if (event.type === 'cash.reset' && event.previous_total_cents != null) {
        const seconds = event.timeout_seconds ?? 20
        setLastActivity(
          `התשלום במזומן התאפס (${formatMoney(event.previous_total_cents)} בוטלו) — ניתן להתחיל מחדש לאחר ${seconds} שניות ללא מטבע נוסף`,
        )
      } else if (event.type === 'access.granted' && event.uid == null) {
        setLastActivity('תשלום מזומן התקבל — הדלת נפתחה')
      } else if (event.type === 'door.opened') {
        setLastActivity('הדלת נפתחה')
      }
    }
    return () => ws.close()
  }, [wsUrl, refreshStatus, showChipToast])

  return {
    gateStatus,
    chipToast,
    lastActivity,
    simError,
    simLoading,
    cashProgress,
    dismissChipToast,
    simulateChip,
    simulateCash,
    formatMoney,
  }
}
