import { useCallback, useEffect, useEffectEvent, useRef, useState } from 'react'
import { NEDARIM_ORIGINS, type NedarimIframeEvent } from '../types/nedarim'
import type { NedarimTransactionResponse } from '../types/topup'

type UseNedarimIframeOptions = {
  /** When false, the message listener is not registered. */
  active: boolean
  onTransactionResponse: (value: NedarimTransactionResponse) => void
  onValidateFields?: (ok: boolean, field?: string, errorType?: string) => void
}

/**
 * Bridges the Nedarim Plus iframe via postMessage.
 *
 * The documentation requires the listener and iframe src to be wired exactly
 * once (v=55) — React strict double-mount is handled by cleanup.
 */
export function useNedarimIframe({
  active,
  onTransactionResponse,
  onValidateFields,
}: UseNedarimIframeOptions) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const [heightPx, setHeightPx] = useState(0)

  const handleTransactionResponse = useEffectEvent(onTransactionResponse)
  const handleValidateFields = useEffectEvent((ok: boolean, field?: string, errorType?: string) => {
    onValidateFields?.(ok, field, errorType)
  })

  useEffect(() => {
    if (!active) return

    function onMessage(event: MessageEvent) {
      if (!(NEDARIM_ORIGINS as readonly string[]).includes(event.origin)) {
        return
      }
      const data = event.data as NedarimIframeEvent | null
      if (!data || typeof data !== 'object' || !('Name' in data)) return

      switch (data.Name) {
        case 'Height': {
          const raw = typeof data.Value === 'string' ? parseInt(data.Value, 10) : Number(data.Value)
          if (Number.isFinite(raw)) setHeightPx(raw + 15)
          break
        }
        case 'ValidateFields': {
          handleValidateFields(data.Value === 'OK', data.Field, data.ErrorType)
          break
        }
        case 'TransactionResponse': {
          handleTransactionResponse(data.Value)
          break
        }
        default:
          break
      }
    }

    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [active])

  const postNedarim = useCallback((data: Record<string, unknown>) => {
    iframeRef.current?.contentWindow?.postMessage(data, '*')
  }, [])

  const requestHeight = useCallback(() => {
    postNedarim({ Name: 'GetHeight' })
  }, [postNedarim])

  const validateFields = useCallback(() => {
    postNedarim({ Name: 'ValidateFields' })
  }, [postNedarim])

  const finishTransaction = useCallback((nedarimTransactionId: string) => {
    postNedarim({ Name: 'FinishTransaction', Value: nedarimTransactionId })
  }, [postNedarim])

  return {
    iframeRef,
    heightPx,
    requestHeight,
    validateFields,
    finishTransaction,
  }
}
