import { useMemo } from 'react'
import type { ReactNode } from 'react'

type UsePageContainerParams = {
  title?: string
  subtitle?: string
  header?: ReactNode
}

export function usePageContainer({ title, subtitle, header }: UsePageContainerParams) {
  const showDefaultHeader = useMemo(
    () => !header && Boolean(title || subtitle),
    [header, title, subtitle],
  )

  return { showDefaultHeader }
}
