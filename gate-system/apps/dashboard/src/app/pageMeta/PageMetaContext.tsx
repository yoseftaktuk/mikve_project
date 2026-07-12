import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

export type PageMeta = {
  title: string
  subtitle?: string
  /** When true, title/subtitle render in page content instead of AppHeader */
  titleInContent?: boolean
  /** When true, shows live gate status badge in AppHeader */
  showLiveStatus?: boolean
}

type PageMetaContextValue = {
  meta: PageMeta
  setMeta: (meta: PageMeta) => void
}

const defaultMeta: PageMeta = { title: 'שער כניסה' }

const PageMetaContext = createContext<PageMetaContextValue | null>(null)

export function PageMetaProvider({ children }: { children: ReactNode }) {
  const [meta, setMetaState] = useState<PageMeta>(defaultMeta)

  const setMeta = useCallback((next: PageMeta) => {
    setMetaState(next)
  }, [])

  const value = useMemo(() => ({ meta, setMeta }), [meta, setMeta])

  return <PageMetaContext.Provider value={value}>{children}</PageMetaContext.Provider>
}

export function usePageMetaContext() {
  const ctx = useContext(PageMetaContext)
  if (!ctx) throw new Error('usePageMetaContext must be used within PageMetaProvider')
  return ctx
}
