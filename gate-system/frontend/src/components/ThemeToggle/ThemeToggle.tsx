import styles from './ThemeToggle.module.css'
import { useThemeToggle } from './useThemeToggle'

export function ThemeToggle() {
  const { toggleTheme, label, icon } = useThemeToggle()

  return (
    <button
      type="button"
      className={styles.button}
      onClick={toggleTheme}
      aria-label={label}
      title={label}
    >
      <span aria-hidden>{icon}</span>
    </button>
  )
}
