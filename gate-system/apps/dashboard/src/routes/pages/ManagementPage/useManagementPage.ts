import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../app/api'
import { managementApi } from '../../../app/managementApi'
import { useManagementAuth } from '../../../hooks/useManagementAuth'

type GateStatus = {
  door_unlock_seconds: number
}

/** Management auth and manual door controls. */
export function useManagementPage() {
  const { authenticated, pin, setPin, pinError, pinLoading, onPinSubmit, logout: clearAuth } = useManagementAuth()

  const [actionError, setActionError] = useState<string | null>(null)
  const [actionSuccess, setActionSuccess] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [gateStatus, setGateStatus] = useState<GateStatus | null>(null)

  useEffect(() => {
    api.get<GateStatus>('/access/healthz').then((r) => setGateStatus(r.data)).catch(() => {})
  }, [])

  const logout = useCallback(() => {
    clearAuth()
    setActionError(null)
    setActionSuccess(null)
  }, [clearAuth])

  const openDoor = useCallback(async () => {
    setLoading(true)
    setActionError(null)
    setActionSuccess(null)
    try {
      await managementApi.post('/access/management/door/open')
      const seconds = gateStatus?.door_unlock_seconds ?? 5
      setActionSuccess(`הדלת נפתחה ל-${seconds} שניות.`)
    } catch {
      setActionError('פתיחת הדלת נכשלה.')
    } finally {
      setLoading(false)
    }
  }, [gateStatus])

  return {
    authenticated,
    pin,
    setPin,
    pinError,
    pinLoading,
    actionError,
    actionSuccess,
    loading,
    gateStatus,
    onPinSubmit,
    logout,
    openDoor,
  }
}
