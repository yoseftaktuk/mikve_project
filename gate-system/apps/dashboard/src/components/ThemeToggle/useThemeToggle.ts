import { useMemo } from 'react'
import { useTheme } from '../../app/theme'

export function useThemeToggle() {
  const { theme, toggleTheme } = useTheme()

  const label = useMemo(
    () => (theme === 'light' ? 'עבור למצב כהה' : 'עבור למצב בהיר'),
    [theme],
  )

  const icon = theme === 'light' ? '🌙' : '☀️'

  return {
    theme,
    toggleTheme,
    label,
    icon,
  }
}
