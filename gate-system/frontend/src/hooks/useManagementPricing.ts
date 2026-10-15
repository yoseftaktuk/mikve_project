import { useCallback, useEffect, useState } from 'react'
import { managementApi } from '../app/managementApi'
import { centsToShekelInput, parseShekelsToCents } from '../app/money'
import type { ManagementPricing, ManagementPricingUpdate } from '../types/managementPricing'

/** Loads and updates entrance fee + monthly subscription price for management. */
export function useManagementPricing(authenticated: boolean) {
  const [entranceShekels, setEntranceShekels] = useState('')
  const [subscriptionShekels, setSubscriptionShekels] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const applyPricing = useCallback((pricing: ManagementPricing) => {
    setEntranceShekels(centsToShekelInput(pricing.entrance_fee_cents))
    setSubscriptionShekels(centsToShekelInput(pricing.subscription_price_cents))
  }, [])

  const refresh = useCallback(async () => {
    if (!authenticated) return
    setLoading(true)
    setError(null)
    try {
      const res = await managementApi.get<ManagementPricing>('/access/management/pricing')
      applyPricing(res.data)
    } catch {
      setError('טעינת המחירים נכשלה.')
    } finally {
      setLoading(false)
    }
  }, [authenticated, applyPricing])

  useEffect(() => {
    if (!authenticated) return

    let cancelled = false
    const load = async () => {
      try {
        const res = await managementApi.get<ManagementPricing>('/access/management/pricing')
        if (cancelled) return
        applyPricing(res.data)
        setError(null)
      } catch {
        if (cancelled) return
        setError('טעינת המחירים נכשלה.')
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [authenticated, applyPricing])

  const save = useCallback(async () => {
    const entranceCents = parseShekelsToCents(entranceShekels)
    const subscriptionCents = parseShekelsToCents(subscriptionShekels)
    if (entranceCents == null) {
      setError('הזן מחיר כניסה תקין בשקלים (למשל 15 או 15.50).')
      return
    }
    if (subscriptionCents == null) {
      setError('הזן מחיר מנוי חודשי תקין בשקלים (למשל 300).')
      return
    }

    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      const body: ManagementPricingUpdate = {
        entrance_fee_cents: entranceCents,
        subscription_price_cents: subscriptionCents,
      }
      const res = await managementApi.patch<ManagementPricing>('/access/management/pricing', body)
      applyPricing(res.data)
      setSuccess('המחירים עודכנו.')
    } catch {
      setError('עדכון המחירים נכשל.')
    } finally {
      setSaving(false)
    }
  }, [entranceShekels, subscriptionShekels, applyPricing])

  return {
    entranceShekels,
    setEntranceShekels,
    subscriptionShekels,
    setSubscriptionShekels,
    loading,
    saving,
    error,
    success,
    refresh,
    save,
  }
}
