import { useCallback, useEffect, useState } from 'react'
import { managementApi } from '../app/managementApi'
import type { ManagementStatistics, StatisticsRangeKey } from '../types/managementStats'

/** Loads aggregated management statistics for the selected date range. */
export function useManagementStats(authenticated: boolean) {
  const [range, setRange] = useState<StatisticsRangeKey>('today')
  const [stats, setStats] = useState<ManagementStatistics | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!authenticated) return
    setLoading(true)
    setError(null)
    try {
      const res = await managementApi.get<ManagementStatistics>('/access/management/statistics', {
        params: { range },
      })
      setStats(res.data)
    } catch {
      setError('טעינת הסטטיסטיקה נכשלה.')
      setStats(null)
    } finally {
      setLoading(false)
    }
  }, [authenticated, range])

  useEffect(() => {
    if (!authenticated) return

    let cancelled = false
    const load = async () => {
      setLoading(true)
      setError(null)
      try {
        const res = await managementApi.get<ManagementStatistics>('/access/management/statistics', {
          params: { range },
        })
        if (cancelled) return
        setStats(res.data)
      } catch {
        if (cancelled) return
        setError('טעינת הסטטיסטיקה נכשלה.')
        setStats(null)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [authenticated, range])

  return {
    range,
    setRange,
    stats: authenticated ? stats : null,
    loading: authenticated ? loading : false,
    error: authenticated ? error : null,
    refresh,
  }
}
