import { Outlet } from 'react-router-dom'
import { PageMetaProvider } from '../../app/pageMeta'
import { AppBottomNav } from './AppNav'
import { AppFooter } from './AppFooter'
import { AppHeader } from './AppHeader'
import styles from './AppLayout.module.css'
import { AppSidebarNav } from './AppNav'

/** Shared shell with header, sidebar/bottom nav, and page outlet. */
export function AppLayout() {
  return (
    <PageMetaProvider>
      <div className={styles.shell} dir="rtl">
        <AppHeader />
        <div className={styles.body}>
          <AppSidebarNav />
          <main className={styles.main} id="main-content">
            <div className={styles.outlet}>
              <Outlet />
            </div>
          </main>
        </div>
        <div className={styles.mobileNavRow}>
          <AppBottomNav />
        </div>
        <AppFooter />
      </div>
    </PageMetaProvider>
  )
}
