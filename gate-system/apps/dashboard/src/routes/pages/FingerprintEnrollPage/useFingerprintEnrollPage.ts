import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../../app/api'
import { formatMoney, parseShekelsToCents } from '../../../app/money'
import { normalizeNationalId } from '../../../app/nationalId'
import type { EnrollStartResponse, EnrollState, EnrollStep } from '../../../types/fingerprint'

const IDLE_STATE: EnrollState = {
  step: 'idle',
  sessionId: null,
  holderName: null,
  nationalId: null,
  slot: null,
  balanceCents: null,
}

const ACTIVE_STEPS = new Set<EnrollStep>(['starting', 'place_finger', 'remove_finger', 'place_again', 'stored'])

const STEP_MESSAGES: Record<EnrollStep, string> = {
  idle: '',
  starting: 'מפעיל את קורא טביעות האצבע…',
  place_finger: 'הנח את האצבע על החיישן והחזק',
  remove_finger: 'הרם את האצבע מהחיישן',
  place_again: 'הנח את אותה אצבע שוב',
  stored: 'טביעת האצבע נשמרה — יוצר כרטיס…',
  registered: 'הרישום הושלם בהצלחה',
  duplicate: 'האצבע הזו כבר רשומה במערכת',
  mismatch: 'שתי הקריאות לא תאמו. נסה שוב.',
  timeout: 'לא זוהתה אצבע בזמן. נסה שוב.',
  cancelled: 'הרישום בוטל',
  failed: 'הרישום נכשל. בדוק את חיבור החיישן ונסה שוב.',
}

const STEP_ORDER: EnrollStep[] = ['place_finger', 'remove_finger', 'place_again', 'registered']

function isEnrollStep(value: unknown): value is EnrollStep {
  return typeof value === 'string' && value in STEP_MESSAGES
}

/** Runs fingerprint enrollment and follows its live progress. */
export function useFingerprintEnrollPage() {
  const [holderName, setHolderName] = useState('')
  const [nationalId, setNationalId] = useState('')
  const [initialAmountShekels, setInitialAmountShekels] = useState('')
  const [enroll, setEnroll] = useState<EnrollState>(IDLE_STATE)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const sessionIdRef = useRef<string | null>(null)

  const wsUrl = useMemo(() => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    return `${proto}://${location.host}/api/access/ws/events`
  }, [])

  useEffect(() => {
    const ws = new WebSocket(wsUrl)
    ws.onmessage = (msg) => {
      let event: {
        type?: string
        session_id?: string
        step?: string
        slot?: number | null
        holder_name?: string | null
        national_id?: string | null
        balance_cents?: number
        reason?: string
      }
      try {
        event = JSON.parse(msg.data)
      } catch {
        return
      }

      if (!event.session_id || event.session_id !== sessionIdRef.current) return

      if (event.type === 'fingerprint.enroll_progress' && isEnrollStep(event.step)) {
        const step = event.step
        if (step === 'failed' && event.reason === 'national_id_taken') {
          sessionIdRef.current = null
          setError('תעודת הזהות כבר רשומה במערכת.')
          setEnroll((current) => ({ ...current, step: 'failed' }))
          return
        }
        setEnroll((current) => ({ ...current, step, slot: event.slot ?? current.slot }))
        return
      }

      if (event.type === 'fingerprint.registered') {
        sessionIdRef.current = null
        setEnroll({
          step: 'registered',
          sessionId: event.session_id,
          holderName: event.holder_name ?? null,
          nationalId: event.national_id ?? null,
          slot: event.slot ?? null,
          balanceCents: event.balance_cents ?? null,
        })
      }
    }
    return () => ws.close()
  }, [wsUrl])

  const start = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      const name = holderName.trim()
      if (name.length < 2) {
        setError('הזן שם מלא (לפחות 2 תווים).')
        return
      }
      const normalizedId = normalizeNationalId(nationalId)
      if (!normalizedId) {
        setError('הזן תעודת זהות ישראלית תקינה (9 ספרות).')
        return
      }
      const initialAmountCents = initialAmountShekels.trim()
        ? parseShekelsToCents(initialAmountShekels)
        : 0
      if (initialAmountCents == null) {
        setError('הזן סכום התחלתי תקין (למשל 50) או השאר ריק.')
        return
      }

      setSubmitting(true)
      setError(null)
      try {
        const res = await api.post<EnrollStartResponse>('/access/management/fingerprint/enroll', {
          holder_name: name,
          national_id: normalizedId,
          initial_amount_cents: initialAmountCents,
        })
        sessionIdRef.current = res.data.session_id
        setEnroll({
          step: 'starting',
          sessionId: res.data.session_id,
          holderName: res.data.holder_name,
          nationalId: res.data.national_id,
          slot: null,
          balanceCents: null,
        })
      } catch (err: unknown) {
        const code =
          err && typeof err === 'object' && 'response' in err
            ? (err as { response?: { data?: { code?: string } } }).response?.data?.code
            : undefined
        if (code === 'invalid_national_id') {
          setError('הזן תעודת זהות ישראלית תקינה (9 ספרות).')
        } else {
          setError('לא ניתן להתחיל רישום. ודא שקורא טביעות האצבע מחובר.')
        }
      } finally {
        setSubmitting(false)
      }
    },
    [holderName, nationalId, initialAmountShekels],
  )

  const cancel = useCallback(async () => {
    const sessionId = sessionIdRef.current
    sessionIdRef.current = null
    setEnroll((current) => ({ ...current, step: 'cancelled' }))
    if (!sessionId) return
    try {
      await api.post('/access/management/fingerprint/enroll/cancel', { session_id: sessionId })
    } catch {
      setError('הביטול לא הגיע לקורא. הרישום יתפוגג מעצמו.')
    }
  }, [])

  const reset = useCallback(() => {
    sessionIdRef.current = null
    setEnroll(IDLE_STATE)
    setError(null)
    setHolderName('')
    setNationalId('')
    setInitialAmountShekels('')
  }, [])

  const isActive = ACTIVE_STEPS.has(enroll.step)
  const stepMessage = STEP_MESSAGES[enroll.step]
  const stepIndex = STEP_ORDER.indexOf(enroll.step)

  return {
    holderName,
    setHolderName,
    nationalId,
    setNationalId,
    initialAmountShekels,
    setInitialAmountShekels,
    enroll,
    error,
    submitting,
    isActive,
    stepMessage,
    stepIndex,
    stepOrder: STEP_ORDER,
    start,
    cancel,
    reset,
    formatMoney,
  }
}
