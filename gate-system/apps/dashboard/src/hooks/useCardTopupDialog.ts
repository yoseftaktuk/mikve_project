import { useCallback, useEffect, useRef, useState } from 'react'
import { useCardTopup } from './useCardTopup'
import { useNedarimIframe } from './useNedarimIframe'

type UseCardTopupDialogParams = {
  chipUid: string
  onClose: () => void
  onPaid: (balanceAfterCents: number) => void
}

/** Glues amount selection, Nedarim iframe clearing, and server status polling. */
export function useCardTopupDialog({ chipUid, onClose, onPaid }: UseCardTopupDialogParams) {
  const topup = useCardTopup(chipUid)
  const [validateError, setValidateError] = useState<string | null>(null)
  const finishRef = useRef<(nedarimTransactionId: string) => void>(() => {})
  const createdIdRef = useRef<string | null>(null)
  const paidNotifiedRef = useRef(false)

  const onClientTransactionResponse = topup.onClientTransactionResponse
  const setPhase = topup.setPhase
  const nedarimTransactionId = topup.created?.nedarim_transaction_id ?? null

  const iframe = useNedarimIframe({
    active: topup.phase === 'ready' || topup.phase === 'submitting' || topup.phase === 'waiting_server',
    onTransactionResponse: (value) => {
      onClientTransactionResponse(value.Status === 'OK', value.Message)
    },
    onValidateFields: (ok, field, errorType) => {
      if (!ok) {
        const label =
          field === 'Card' ? 'מספר כרטיס' : field === 'Expiration' ? 'תוקף' : field === 'CVV' ? 'CVV' : 'שדה'
        setValidateError(errorType === 'Empty' ? `${label} ריק` : `${label} אינו תקין`)
        setPhase('ready')
        return
      }
      setValidateError(null)
      const id = createdIdRef.current
      if (id) finishRef.current(id)
    },
  })

  useEffect(() => {
    createdIdRef.current = nedarimTransactionId
  }, [nedarimTransactionId])

  useEffect(() => {
    finishRef.current = iframe.finishTransaction
  }, [iframe.finishTransaction])

  useEffect(() => {
    if (topup.phase === 'choose_amount') {
      paidNotifiedRef.current = false
    }
    if (topup.phase === 'paid' && !paidNotifiedRef.current) {
      paidNotifiedRef.current = true
      onPaid(topup.status?.balance_after_cents ?? 0)
    }
  }, [topup.phase, topup.status?.balance_after_cents, onPaid])

  const pay = useCallback(() => {
    if (!topup.created || topup.phase !== 'ready') return
    setValidateError(null)
    topup.markSubmitting()
    iframe.validateFields()
  }, [iframe, topup])

  const close = useCallback(async () => {
    await topup.cancelPendingTopup()
    onClose()
  }, [onClose, topup])

  return {
    phase: topup.phase,
    amountsCents: topup.amountsCents,
    created: topup.created,
    status: topup.status,
    error: topup.error,
    clientMessage: topup.clientMessage,
    startTopup: topup.startTopup,
    iframeRef: iframe.iframeRef,
    heightPx: iframe.heightPx,
    requestHeight: iframe.requestHeight,
    validateError,
    pay,
    close,
  }
}
