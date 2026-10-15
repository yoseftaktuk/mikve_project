import { NavLink } from 'react-router-dom'
import styles from './AppNav.module.css'
import { useAppNav } from './useAppNav'

function navClassName(isActive: boolean, base: string) {
  return [base, isActive ? styles.active : ''].filter(Boolean).join(' ')
}

export function AppSidebarNav() {
  const { items, activePath } = useAppNav()

  return (
    <nav className={styles.sidebar} aria-label="ניווט ראשי">
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={() => navClassName(activePath === item.to, styles.link)}
          end={item.to === '/dashboard'}
        >
          <span className={styles.icon} aria-hidden>
            {item.icon}
          </span>
          <span>{item.label}</span>
        </NavLink>
      ))}
    </nav>
  )
}

export function AppBottomNav() {
  const { items, activePath } = useAppNav()

  return (
    <nav className={styles.bottomNav} aria-label="ניווט תחתון">
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={() => navClassName(activePath === item.to, `${styles.link} ${styles.bottomLink}`)}
          end={item.to === '/dashboard'}
        >
          <span className={styles.icon} aria-hidden>
            {item.icon}
          </span>
          <span>{item.shortLabel}</span>
        </NavLink>
      ))}
    </nav>
  )
}
