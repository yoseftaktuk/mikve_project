import type { ReactNode } from 'react'
import { ThemeToggle } from '../ThemeToggle'
import styles from './PageContainer.module.css'
import { usePageContainer } from './usePageContainer'

type PageContainerProps = {
  children: ReactNode
  title?: string
  subtitle?: string
  header?: ReactNode
}

export function PageContainer({ children, title, subtitle, header }: PageContainerProps) {
  const { showDefaultHeader } = usePageContainer({ title, subtitle, header })

  return (
    <div className={styles.page} dir="rtl">
      <ThemeToggle />
      {header ??
        (showDefaultHeader ? (
          <header className={styles.header}>
            {title && <h1>{title}</h1>}
            {subtitle && <p>{subtitle}</p>}
          </header>
        ) : null)}
      {children}
    </div>
  )
}
