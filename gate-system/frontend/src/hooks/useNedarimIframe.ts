import { useCallback, useEffect, useEffectEvent, useRef, useState } from 'react'
import { NEDARIM_ORIGINS, type NedarimIframeEvent } from '../types/nedarim'
import type { NedarimTransactionResponse } from '../types/topup'

const IFRAME_LOAD_TIMEOUT_MS = 8000
const IFRAME_LOAD_ERROR =
  'לא ניתן לטעון את מסך התשלום של נדרים פלוס. בדקו חיבור ורעננו.'

type UseNedarimIframeOptions = {
  /** When false, the message listener is not registered. */
  active: boolean
  onTransactionResponse: (value: NedarimTransactionResponse) => void
  onValidateFields?: (ok: boolean, field?: string, errorType?: string) => void
  /** Called when the iframe fails to load or never signals from Nedarim. */
  onLoadFailure?: (message: string) => void
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
  onLoadFailure,
}: UseNedarimIframeOptions) {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const [heightPx, setHeightPx] = useState(0)
  const heardFromNedarimRef = useRef(false)
  const loadTimerRef = useRef<number | null>(null)
  const onLoadFailureRef = useRef(onLoadFailure)

  useEffect(() => {
    onLoadFailureRef.current = onLoadFailure
  }, [onLoadFailure])

  const handleTransactionResponse = useEffectEvent(onTransactionResponse)
  const handleValidateFields = useEffectEvent((ok: boolean, field?: string, errorType?: string) => {
    onValidateFields?.(ok, field, errorType)
  })

  const clearLoadTimer = useCallback(() => {
    if (loadTimerRef.current != null) {
      window.clearTimeout(loadTimerRef.current)
      loadTimerRef.current = null
    }
  }, [])

  const markNedarimAlive = useCallback(() => {
    heardFromNedarimRef.current = true
    clearLoadTimer()
  }, [clearLoadTimer])

  useEffect(() => {
    if (!active) {
      clearLoadTimer()
      heardFromNedarimRef.current = false
      return
    }

    function onMessage(event: MessageEvent) {
      if (!(NEDARIM_ORIGINS as readonly string[]).includes(event.origin)) {
        return
      }
      const data = event.data as NedarimIframeEvent | null
      if (!data || typeof data !== 'object' || !('Name' in data)) return

      markNedarimAlive()

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
    return () => {
      window.removeEventListener('message', onMessage)
      clearLoadTimer()
    }
  }, [active, clearLoadTimer, markNedarimAlive])

  const beginLoadWatch = useCallback(() => {
    if (!active) return
    heardFromNedarimRef.current = false
    clearLoadTimer()
    loadTimerRef.current = window.setTimeout(() => {
      if (!heardFromNedarimRef.current) {
        onLoadFailureRef.current?.(IFRAME_LOAD_ERROR)
      }
    }, IFRAME_LOAD_TIMEOUT_MS)
  }, [active, clearLoadTimer])

  const onIframeError = useCallback(() => {
    clearLoadTimer()
    onLoadFailureRef.current?.(IFRAME_LOAD_ERROR)
  }, [clearLoadTimer])

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
    beginLoadWatch,
    onIframeError,
  }
}
