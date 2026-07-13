import { useContext } from 'react'
import { ThemeContext } from './ThemeContext'

/** Access the current theme and theme toggles from ThemeProvider. */
export function useTheme() {
  const context = useContext(ThemeContext)
  if (!context) {
    throw new Error('useTheme must be used within ThemeProvider')
  }
  return context
}
