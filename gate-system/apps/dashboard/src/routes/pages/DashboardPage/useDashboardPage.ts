import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../../app/api'
import { formatMoney } from '../../../app/money'
import type { ChipToastData } from '../../../components/ChipToast/types'
import type { GateStatus } from '../../../components/GateEntrancePanel'
import type { PendingApproval } from '../../../types/fingerprint'
import type { TopupOffer } from '../../../types/topup'

type WsEvent = {
  type?: string
  method?: string
  uid?: string | null
  chip_id?: string
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
  approval_id?: string
  holder_name?: string | null
  expires_in_seconds?: number
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

export { formatMoney }

function grantedToast(event: {
  balance_after_cents?: number
  remaining_cents?: number
  method?: string
  holder_name?: string | null
}): ChipToastData {
  const isCash = event.method === 'cash'
  const changeCents = event.remaining_cents ?? 0
  const name = event.holder_name?.trim()
  return {
    kind: 'granted',
    title: name ? `שלום ${name}` : 'הדלת נפתחה',
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

function fingerprintDeniedToast(event: WsEvent): ChipToastData {
  const name = event.holder_name?.trim()
  if (event.reason === 'insufficient_balance') {
    return {
      kind: 'denied',
      title: name ? `${name} — אין מספיק יתרה` : 'אין מספיק יתרה',
      message:
        event.fee_cents != null
          ? `נדרשים ${formatMoney(event.fee_cents)} לכניסה. פנה לדלפק לטעינת יתרה.`
          : 'אין מספיק יתרה. פנה לדלפק לטעינת יתרה.',
      balanceCents: event.balance_cents ?? null,
    }
  }
  if (event.reason === 'chip_disabled') {
    return {
      kind: 'denied',
      title: name ? `${name} — חסום` : 'המשתמש חסום',
      message: 'הכניסה עבור טביעת האצבע הזו אינה פעילה. פנה למנהל המערכת.',
      balanceCents: event.balance_cents ?? null,
    }
  }
  return {
    kind: 'denied',
    title: 'טביעת אצבע לא מזוהה',
    message: 'טביעת האצבע לא רשומה במערכת. פנה לדלפק לרישום.',
    balanceCents: null,
  }
}

function chipToastFromEvent(event: WsEvent): ChipToastData | null {
  if (isCashGrantedEvent(event)) {
    return grantedToast({ method: 'cash', remaining_cents: event.remaining_cents })
  }

  if (event.type === 'access.granted' && event.uid) {
    return grantedToast(event)
  }

  if (event.type === 'access.denied' && event.method === 'fingerprint') {
    return fingerprintDeniedToast(event)
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

/** Tracks live gate status, WebSocket events, and cash/chip simulation for the entrance screen. */
export function useDashboardPage() {
  const [gateStatus, setGateStatus] = useState<GateStatus | null>(null)
  const [chipToast, setChipToast] = useState<ChipToastData | null>(null)
  const [lastActivity, setLastActivity] = useState<string | null>(null)
  const [simError, setSimError] = useState<string | null>(null)
  const [simLoading, setSimLoading] = useState(false)
  const [pendingApproval, setPendingApproval] = useState<PendingApproval | null>(null)
  const [approvalSubmitting, setApprovalSubmitting] = useState(false)
  const [approvalError, setApprovalError] = useState<string | null>(null)
  const [topupOffer, setTopupOffer] = useState<TopupOffer | null>(null)
  const [cardTopupOpen, setCardTopupOpen] = useState(false)
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

  const approvePending = useCallback(async () => {
    if (!pendingApproval) return
    setApprovalSubmitting(true)
    setApprovalError(null)
    try {
      await api.post('/access/fingerprint/approve', { approval_id: pendingApproval.approvalId })
      setPendingApproval(null)
      refreshStatus()
    } catch {
      setApprovalError('האישור נכשל או פג. בקש מהנכנס לסרוק שוב.')
    } finally {
      setApprovalSubmitting(false)
    }
  }, [pendingApproval, refreshStatus])

  const cancelPending = useCallback(async () => {
    if (!pendingApproval) return
    const approvalId = pendingApproval.approvalId
    setPendingApproval(null)
    setApprovalError(null)
    try {
      await api.post('/access/fingerprint/cancel', { approval_id: approvalId })
    } catch {
      // The approval expires on its own, so a failed cancel needs no user action.
    }
  }, [pendingApproval])

  const dismissTopupOffer = useCallback(() => {
    setTopupOffer(null)
    setCardTopupOpen(false)
  }, [])

  const chooseCoinsTopup = useCallback(() => {
    setTopupOffer(null)
    setCardTopupOpen(false)
    setLastActivity('הכנס מטבעות לתשלום עלות הכניסה')
  }, [])

  const chooseCardTopup = useCallback(() => {
    setCardTopupOpen(true)
  }, [])

  const onCardTopupPaid = useCallback((balanceAfterCents: number) => {
    setLastActivity(`יתרה נטענה בהצלחה — ${formatMoney(balanceAfterCents)}. סרוק שוב לכניסה.`)
  }, [])

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

      if (event.type === 'access.pending' && event.approval_id) {
        setApprovalError(null)
        setTopupOffer(null)
        setCardTopupOpen(false)
        setPendingApproval({
          approvalId: event.approval_id,
          uid: event.uid ?? '',
          holderName: event.holder_name ?? null,
          balanceCents: event.balance_cents ?? 0,
          feeCents: event.fee_cents ?? 0,
          expiresInSeconds: event.expires_in_seconds ?? 25,
        })
        setLastActivity(
          event.holder_name
            ? `טביעת אצבע זוהתה — ${event.holder_name} ממתין לאישור`
            : 'טביעת אצבע זוהתה — ממתין לאישור',
        )
        return
      }

      if (event.type === 'access.topup_needed' && event.uid && event.chip_id) {
        setPendingApproval(null)
        setChipToast(null)
        setCardTopupOpen(false)
        setTopupOffer({
          uid: event.uid,
          chipId: event.chip_id,
          holderName: event.holder_name ?? null,
          balanceCents: event.balance_cents ?? 0,
          feeCents: event.fee_cents ?? 0,
        })
        setLastActivity(
          event.holder_name
            ? `${event.holder_name} — אין מספיק יתרה, ממתין לבחירת טעינה`
            : 'אין מספיק יתרה — ממתין לבחירת טעינה',
        )
        return
      }

      if (
        event.type === 'access.pending_cleared' ||
        event.type === 'access.granted' ||
        event.type === 'access.denied'
      ) {
        setPendingApproval((current) =>
          current && (!event.approval_id || event.approval_id === current.approvalId) ? null : current,
        )
      }

      const toast = chipToastFromEvent(event)
      if (toast) {
        showChipToast(toast)
      }

      if (event.type === 'access.pending_cleared') {
        setLastActivity(
          event.reason === 'timeout' ? 'האישור פג — נדרשת סריקה חדשה' : 'האישור בוטל',
        )
        return
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
    pendingApproval,
    approvalSubmitting,
    approvalError,
    topupOffer,
    cardTopupOpen,
    dismissChipToast,
    simulateChip,
    simulateCash,
    approvePending,
    cancelPending,
    dismissTopupOffer,
    chooseCoinsTopup,
    chooseCardTopup,
    onCardTopupPaid,
    formatMoney,
  }
}
