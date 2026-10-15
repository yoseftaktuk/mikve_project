import { LiveStatusBadge } from '../LiveStatusBadge'
import { ThemeToggle } from '../ThemeToggle'
import { usePageMetaContext } from '../../app/pageMeta/PageMetaContext'
import styles from './AppHeader.module.css'

export function AppHeader() {
  const { meta } = usePageMetaContext()

  const showBrand = meta.title && !meta.titleInContent

  return (
    <header className={styles.header}>
      {meta.showLiveStatus && (
        <div className={styles.liveStatus}>
          <LiveStatusBadge />
        </div>
      )}
      {showBrand && (
        <div className={styles.brand}>
          <h1 className={styles.title}>{meta.title}</h1>
          {meta.subtitle && <p className={styles.subtitle}>{meta.subtitle}</p>}
        </div>
      )}
      <div className={styles.actions}>
        <ThemeToggle />
      </div>
    </header>
  )
}
