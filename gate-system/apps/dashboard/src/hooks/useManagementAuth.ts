import { useCallback, useEffect, useState } from 'react'
import { managementApi, setManagementUnauthorizedHandler } from '../app/managementApi'
import type { ManagementAuthResponse } from '../types/managementAuth'

/** PIN login state shared by every management-protected screen. */
export function useManagementAuth() {
  const [authenticated, setAuthenticated] = useState(false)
  const [authChecking, setAuthChecking] = useState(true)
  const [pin, setPin] = useState('')
  const [pinError, setPinError] = useState<string | null>(null)
  const [pinLoading, setPinLoading] = useState(false)

  useEffect(() => {
    setManagementUnauthorizedHandler(() => setAuthenticated(false))
    return () => setManagementUnauthorizedHandler(null)
  }, [])

  useEffect(() => {
    let cancelled = false
    const checkSession = async () => {
      try {
        const res = await managementApi.get<ManagementAuthResponse>('/access/management/session')
        if (!cancelled) {
          setAuthenticated(res.data.authenticated)
        }
      } catch {
        if (!cancelled) {
          setAuthenticated(false)
        }
      } finally {
        if (!cancelled) {
          setAuthChecking(false)
        }
      }
    }
    void checkSession()
    return () => {
      cancelled = true
    }
  }, [])

  const onPinSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      setPinLoading(true)
      setPinError(null)
      try {
        const res = await managementApi.post<ManagementAuthResponse>('/access/management/auth', { pin })
        setAuthenticated(res.data.authenticated)
        setPin('')
      } catch {
        setPinError('קוד שגוי. נסה שוב.')
      } finally {
        setPinLoading(false)
      }
    },
    [pin],
  )

  const logout = useCallback(async () => {
    try {
      await managementApi.post<ManagementAuthResponse>('/access/management/logout')
    } catch {
      // Cookie/session may already be gone; still clear local auth state.
    } finally {
      setAuthenticated(false)
    }
  }, [])

  return { authenticated, authChecking, pin, setPin, pinError, pinLoading, onPinSubmit, logout }
}
