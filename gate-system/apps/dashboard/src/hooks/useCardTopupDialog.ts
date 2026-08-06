import { useCallback, useEffect, useRef, useState } from 'react'
import { extractApiErrorMessage } from '../app/paymentsApi'
import { useCardTopup } from './useCardTopup'
import { useNedarimIframe } from './useNedarimIframe'

type UseCardTopupDialogParams = {
  chipUid: string
  onClose: () => void
  onPaid: (balanceAfterCents: number) => void
}

/** Glues amount selection, Nedarim iframe or mock pay, and server status polling. */
export function useCardTopupDialog({ chipUid, onClose, onPaid }: UseCardTopupDialogParams) {
  const topup = useCardTopup(chipUid)
  const [validateError, setValidateError] = useState<string | null>(null)
  const finishRef = useRef<(nedarimTransactionId: string) => void>(() => {})
  const createdIdRef = useRef<string | null>(null)
  const paidNotifiedRef = useRef(false)
  const isMock = topup.paymentMode === 'mock'

  const onClientTransactionResponse = topup.onClientTransactionResponse
  const setPhase = topup.setPhase
  const nedarimTransactionId = topup.created?.nedarim_transaction_id ?? null

  const iframe = useNedarimIframe({
    active:
      !isMock &&
      (topup.phase === 'ready' || topup.phase === 'submitting' || topup.phase === 'waiting_server'),
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
    onLoadFailure: (message) => {
      setValidateError(null)
      topup.setError(message)
      setPhase('failed')
    },
  })

  const beginLoadWatch = iframe.beginLoadWatch
  const finishTransaction = iframe.finishTransaction

  useEffect(() => {
    createdIdRef.current = nedarimTransactionId
  }, [nedarimTransactionId])

  useEffect(() => {
    finishRef.current = finishTransaction
  }, [finishTransaction])

  useEffect(() => {
    if (!isMock && topup.phase === 'ready' && topup.created?.iframe_url) {
      beginLoadWatch()
    }
  }, [isMock, topup.phase, topup.created?.iframe_url, beginLoadWatch])

  useEffect(() => {
    if (topup.phase === 'choose_amount') {
      paidNotifiedRef.current = false
    }
    if (topup.phase === 'paid' && !paidNotifiedRef.current) {
      paidNotifiedRef.current = true
      onPaid(topup.status?.balance_after_cents ?? 0)
    }
  }, [topup.phase, topup.status?.balance_after_cents, onPaid])

  const pay = useCallback(async () => {
    if (!topup.created || topup.phase !== 'ready') return
    setValidateError(null)

    if (isMock) {
      topup.markSubmitting()
      try {
        const result = await topup.simulateMockPay(topup.created.topup_id)
        if (result.status !== 'ok') {
          setValidateError(result.message || 'סימולציית התשלום נכשלה')
          setPhase('ready')
          return
        }
        topup.onClientTransactionResponse(true)
      } catch (err) {
        setValidateError(extractApiErrorMessage(err))
        setPhase('ready')
      }
      return
    }

    topup.markSubmitting()
    iframe.validateFields()
  }, [iframe, isMock, setPhase, topup])

  const close = useCallback(async () => {
    await topup.cancelPendingTopup()
    onClose()
  }, [onClose, topup])

  return {
    phase: topup.phase,
    paymentMode: topup.paymentMode,
    amountsCents: topup.amountsCents,
    created: topup.created,
    status: topup.status,
    error: topup.error,
    clientMessage: topup.clientMessage,
    startTopup: topup.startTopup,
    iframeRef: iframe.iframeRef,
    heightPx: iframe.heightPx,
    requestHeight: iframe.requestHeight,
    onIframeError: iframe.onIframeError,
    validateError,
    pay,
    close,
  }
}
