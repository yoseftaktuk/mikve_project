import { useEffect } from 'react'
import type { PageMeta } from './PageMetaContext'
import { usePageMetaContext } from './PageMetaContext'

export function usePageMeta(meta: PageMeta) {
  const { setMeta } = usePageMetaContext()

  useEffect(() => {
    setMeta(meta)
  }, [meta.title, meta.subtitle, meta.titleInContent, meta.showLiveStatus, setMeta])
}
