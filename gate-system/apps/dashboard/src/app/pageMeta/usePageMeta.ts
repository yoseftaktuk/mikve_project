import { useEffect } from 'react'
import type { PageMeta } from './PageMetaContext'
import { usePageMetaContext } from './PageMetaContext'

/** Set the current page title/subtitle while this page is mounted. */
export function usePageMeta(meta: PageMeta) {
  const { setMeta } = usePageMetaContext()

  useEffect(() => {
    setMeta(meta)
    // Intentionally depend on meta fields so inline page objects don't retrigger every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- meta object identity is unstable
  }, [meta.title, meta.subtitle, meta.titleInContent, meta.showLiveStatus, setMeta])
}
