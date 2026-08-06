import { useCallback, useEffect, useState } from 'react'
import { managementApi } from '../app/managementApi'
import type { ManagementUser, ManagementUserUpdate } from '../types/managementUsers'

/** Loads and mutates registered ledger users for the management page. */
export function useManagementUsers(authenticated: boolean) {
  const [users, setUsers] = useState<ManagementUser[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [editEnabled, setEditEnabled] = useState(true)
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
    setEditingId(user.chip_id)
    setEditName(user.holder_name ?? '')
    setEditEnabled(user.is_enabled)
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
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      const body: ManagementUserUpdate = {
        holder_name: name.length ? name : null,
        is_enabled: editEnabled,
      }
      const res = await managementApi.patch<ManagementUser>(
        `/access/management/users/${encodeURIComponent(editingId)}`,
        body,
      )
      setUsers((prev) => prev.map((u) => (u.chip_id === editingId ? res.data : u)))
      setEditingId(null)
      setSuccess('הרשום עודכן.')
    } catch {
      setError('עדכון הרשום נכשל.')
    } finally {
      setSaving(false)
    }
  }, [editingId, editName, editEnabled])

  const deleteUser = useCallback(async (user: ManagementUser) => {
    const label = user.holder_name?.trim() || user.uid
    const ok = window.confirm(`למחוק את ${label}? פעולה זו מוחקת את טביעת האצבע והיתרה.`)
    if (!ok) return
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      await managementApi.delete(`/access/management/users/${encodeURIComponent(user.chip_id)}`)
      setUsers((prev) => prev.filter((u) => u.chip_id !== user.chip_id))
      if (editingId === user.chip_id) setEditingId(null)
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
    editEnabled,
    setEditEnabled,
    saving,
    refresh,
    startEdit,
    cancelEdit,
    saveEdit,
    deleteUser,
  }
}
