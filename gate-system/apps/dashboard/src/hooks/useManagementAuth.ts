import { useCallback, useState } from 'react'
import { managementApi } from '../app/managementApi'
import { clearManagementToken, getManagementToken, setManagementToken } from '../app/managementStorage'

/** PIN login state shared by every management-protected screen. */
export function useManagementAuth() {
  const [authenticated, setAuthenticated] = useState(() => Boolean(getManagementToken()))
  const [pin, setPin] = useState('')
  const [pinError, setPinError] = useState<string | null>(null)
  const [pinLoading, setPinLoading] = useState(false)

  const onPinSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      setPinLoading(true)
      setPinError(null)
      try {
        const res = await managementApi.post<{ token: string }>('/access/management/auth', { pin })
        setManagementToken(res.data.token)
        setAuthenticated(true)
        setPin('')
      } catch {
        setPinError('קוד שגוי. נסה שוב.')
      } finally {
        setPinLoading(false)
      }
    },
    [pin],
  )

  const logout = useCallback(() => {
    clearManagementToken()
    setAuthenticated(false)
  }, [])

  return { authenticated, pin, setPin, pinError, pinLoading, onPinSubmit, logout }
}
