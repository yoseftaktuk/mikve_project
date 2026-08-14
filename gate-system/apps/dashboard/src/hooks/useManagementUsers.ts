import { useCallback, useEffect, useState } from 'react'
import { managementApi } from '../app/managementApi'
import { centsToShekelInput, parseNonNegativeShekelsToCents } from '../app/money'
import { normalizeNationalId } from '../app/nationalId'
import type { ManagementUser, ManagementUserUpdate } from '../types/managementUsers'

/** Loads and mutates registered ledger users for the management page. */
export function useManagementUsers(authenticated: boolean) {
  const [users, setUsers] = useState<ManagementUser[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [editNationalId, setEditNationalId] = useState('')
  const [editEnabled, setEditEnabled] = useState(true)
  const [editBalanceShekels, setEditBalanceShekels] = useState('')
  const [saving, setSaving] = useState(false)

  const refresh = useCallback(async () => {
    if (!authenticated) return
    setLoading(true)
    setError(null)
    try {
      const res = await managementApi.get<ManagementUser[]>('/access/management/users')
      setUsers(res.data)
    } catch {
      setError('טעינת הרשומים נכשלה.')
    } finally {
      setLoading(false)
    }
  }, [authenticated])

  useEffect(() => {
    if (!authenticated) return

    let cancelled = false
    const load = async () => {
      try {
        const res = await managementApi.get<ManagementUser[]>('/access/management/users')
        if (cancelled) return
        setUsers(res.data)
        setError(null)
      } catch {
        if (cancelled) return
        setError('טעינת הרשומים נכשלה.')
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [authenticated])

  const visibleUsers = authenticated ? users : []

  const startEdit = useCallback((user: ManagementUser) => {
    setEditingId(user.fingerprint_id)
    setEditName(user.holder_name ?? '')
    setEditNationalId(user.national_id ?? '')
    setEditEnabled(user.is_enabled)
    setEditBalanceShekels(centsToShekelInput(user.balance_cents))
    setError(null)
    setSuccess(null)
  }, [])

  const cancelEdit = useCallback(() => {
    setEditingId(null)
  }, [])

  const saveEdit = useCallback(async () => {
    if (!editingId) return
    const name = editName.trim()
    if (name.length > 0 && name.length < 2) {
      setError('השם חייב להכיל לפחות שני תווים (או להיות ריק).')
      return
    }
    const nationalIdRaw = editNationalId.trim()
    let nationalId: string | null = null
    if (nationalIdRaw) {
      nationalId = normalizeNationalId(nationalIdRaw)
      if (!nationalId) {
        setError('הזן תעודת זהות ישראלית תקינה (9 ספרות) או השאר ריק.')
        return
      }
    }
    const balanceCents = parseNonNegativeShekelsToCents(editBalanceShekels)
    if (balanceCents == null) {
      setError('הזן יתרה תקינה בשקלים (למשל 10 או 5.50).')
      return
    }
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      const body: ManagementUserUpdate = {
        holder_name: name.length ? name : null,
        national_id: nationalId,
        is_enabled: editEnabled,
        balance_cents: balanceCents,
      }
      const res = await managementApi.patch<ManagementUser>(
        `/access/management/users/${encodeURIComponent(editingId)}`,
        body,
      )
      setUsers((prev) => prev.map((u) => (u.fingerprint_id === editingId ? res.data : u)))
      setEditingId(null)
      setSuccess('הרשום עודכן.')
    } catch (err: unknown) {
      const detail =
        err && typeof err === 'object' && 'response' in err
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
          : undefined
      if (detail === 'national_id_taken') {
        setError('תעודת הזהות כבר רשומה על משתמש אחר.')
      } else if (detail === 'invalid_national_id') {
        setError('הזן תעודת זהות ישראלית תקינה (9 ספרות) או השאר ריק.')
      } else {
        setError('עדכון הרשום נכשל.')
      }
    } finally {
      setSaving(false)
    }
  }, [editingId, editName, editNationalId, editEnabled, editBalanceShekels])

  const deleteUser = useCallback(async (user: ManagementUser) => {
    const label = user.holder_name?.trim() || user.uid
    const ok = window.confirm(`למחוק את ${label}? פעולה זו מוחקת את טביעת האצבע והיתרה.`)
    if (!ok) return
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      await managementApi.delete(`/access/management/users/${encodeURIComponent(user.fingerprint_id)}`)
      setUsers((prev) => prev.filter((u) => u.fingerprint_id !== user.fingerprint_id))
      if (editingId === user.fingerprint_id) setEditingId(null)
      setSuccess('הרשום נמחק.')
    } catch {
      setError('מחיקת הרשום נכשלה.')
    } finally {
      setSaving(false)
    }
  }, [editingId])

  return {
    users: visibleUsers,
    loading: authenticated ? loading : false,
    error: authenticated ? error : null,
    success: authenticated ? success : null,
    editingId: authenticated ? editingId : null,
    editName,
    setEditName,
    editNationalId,
    setEditNationalId,
    editEnabled,
    setEditEnabled,
    editBalanceShekels,
    setEditBalanceShekels,
    saving,
    refresh,
    startEdit,
    cancelEdit,
    saveEdit,
    deleteUser,
  }
}
