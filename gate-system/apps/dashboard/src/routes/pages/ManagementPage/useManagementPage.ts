import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../app/api'
import { managementApi } from '../../../app/managementApi'
import { clearManagementToken, getManagementToken, setManagementToken } from '../../../app/managementStorage'

type GateStatus = {
  door_unlock_seconds: number
}

type ChipInfo = {
  uid: string
  chip_id: string
  balance_cents: number
  is_enabled: boolean
}

type TopupResult = {
  uid: string
  chip_id: string
  balance_cents: number
  added_cents: number
}

function formatMoney(cents: number) {
  return `₪${(cents / 100).toFixed(2)}`
}

function parseShekelsToCents(value: string): number | null {
  const normalized = value.trim().replace(',', '.')
  if (!normalized) return null
  const shekels = Number(normalized)
  if (!Number.isFinite(shekels) || shekels <= 0) return null
  return Math.round(shekels * 100)
}

/** Management auth, chip lookup/top-up, and manual door controls. */
export function useManagementPage() {
  const [authenticated, setAuthenticated] = useState(() => Boolean(getManagementToken()))
  const [pin, setPin] = useState('')
  const [pinError, setPinError] = useState<string | null>(null)
  const [pinLoading, setPinLoading] = useState(false)

  const [uid, setUid] = useState('')
  const [amountShekels, setAmountShekels] = useState('')
  const [chipInfo, setChipInfo] = useState<ChipInfo | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionSuccess, setActionSuccess] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [gateStatus, setGateStatus] = useState<GateStatus | null>(null)

  useEffect(() => {
    api.get<GateStatus>('/access/healthz').then((r) => setGateStatus(r.data)).catch(() => {})
  }, [])

  const onPinSubmit = useCallback(async (e: React.FormEvent) => {
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
  }, [pin])

  const logout = useCallback(() => {
    clearManagementToken()
    setAuthenticated(false)
    setChipInfo(null)
    setActionError(null)
    setActionSuccess(null)
  }, [])

  const lookupChip = useCallback(async () => {
    if (!uid.trim()) {
      setActionError("הזן מזהה צ'יפ.")
      return
    }
    setLoading(true)
    setActionError(null)
    setActionSuccess(null)
    try {
      const res = await managementApi.get<ChipInfo>(`/access/management/chip/${encodeURIComponent(uid.trim())}`)
      setChipInfo(res.data)
    } catch {
      setChipInfo(null)
      setActionError("צ'יפ לא נמצא. ניתן להטעין צ'יפ חדש — הוא יירשם אוטומטית.")
    } finally {
      setLoading(false)
    }
  }, [uid])

  const topupChip = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault()
      const amountCents = parseShekelsToCents(amountShekels)
      if (!uid.trim()) {
        setActionError("הזן מזהה צ'יפ.")
        return
      }
      if (amountCents == null) {
        setActionError('הזן סכום תקין להטענה (למשל 10 או 5.50).')
        return
      }
      setLoading(true)
      setActionError(null)
      setActionSuccess(null)
      try {
        const res = await managementApi.post<TopupResult>('/access/management/chip/topup', {
          uid: uid.trim(),
          amount_cents: amountCents,
        })
        setChipInfo({
          uid: res.data.uid,
          chip_id: res.data.chip_id,
          balance_cents: res.data.balance_cents,
          is_enabled: true,
        })
        setActionSuccess(`הצ'יפ הוטען ב-${formatMoney(res.data.added_cents)}. יתרה נוכחית: ${formatMoney(res.data.balance_cents)}`)
        setAmountShekels('')
      } catch {
        setActionError("הטענת הצ'יפ נכשלה.")
      } finally {
        setLoading(false)
      }
    },
    [uid, amountShekels],
  )

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
  }, [gateStatus?.door_unlock_seconds])

  return {
    authenticated,
    pin,
    setPin,
    pinError,
    pinLoading,
    uid,
    setUid,
    amountShekels,
    setAmountShekels,
    chipInfo,
    actionError,
    actionSuccess,
    loading,
    gateStatus,
    formatMoney,
    onPinSubmit,
    logout,
    lookupChip,
    topupChip,
    openDoor,
  }
}
