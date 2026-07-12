import styles from './LiveStatusBadge.module.css'

export function LiveStatusBadge() {
  return (
    <span className={styles.badge}>
      <span className={styles.dot} aria-hidden />
      המערכת פעילה ומאזינה
    </span>
  )
}
