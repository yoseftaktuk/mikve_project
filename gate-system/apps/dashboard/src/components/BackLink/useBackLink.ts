import { useMemo } from 'react'
import styles from './BackLink.module.css'

type UseBackLinkParams = {
  inline?: boolean
}

export function useBackLink({ inline }: UseBackLinkParams) {
  const className = useMemo(
    () => [styles.link, inline ? styles.linkInline : ''].filter(Boolean).join(' '),
    [inline],
  )

  return { className }
}
