import { useEffect } from 'react'
import type { PageMeta } from './PageMetaContext'
import { usePageMetaContext } from './PageMetaContext'

/** Set the current page title/subtitle while this page is mounted. */
export function usePageMeta(meta: PageMeta) {
  const { setMeta } = usePageMetaContext()

  useEffect(() => {
    setMeta(meta)
  }, [meta.title, meta.subtitle, meta.titleInContent, meta.showLiveStatus, setMeta])
}
