import { useMemo } from 'react'
import { useLocation } from 'react-router-dom'

export type NavItem = {
  to: string
  label: string
  shortLabel: string
  icon: string
}

const NAV_ITEMS: NavItem[] = [
  { to: '/dashboard', label: 'מסך כניסה', shortLabel: 'כניסה', icon: '🚪' },
  { to: '/charge-chip', label: "טעינת צ'יפ", shortLabel: 'טעינה', icon: '💳' },
  { to: '/management', label: 'ניהול', shortLabel: 'ניהול', icon: '⚙️' },
]

/** Builds navigation items and highlights the active route. */
export function useAppNav() {
  const { pathname } = useLocation()

  const activePath = useMemo(() => {
    if (pathname.startsWith('/management') || pathname.startsWith('/admin')) return '/management'
    if (pathname.startsWith('/charge-chip')) return '/charge-chip'
    return '/dashboard'
  }, [pathname])

  return { items: NAV_ITEMS, activePath }
}
