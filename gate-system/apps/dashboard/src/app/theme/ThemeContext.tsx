import { createContext, useCallback, useLayoutEffect, useMemo, useState, type ReactNode } from 'react'
import type { Theme } from './types'
import { applyThemeToDocument, getInitialTheme, writeStoredTheme } from './themeStorage'

type ThemeContextValue = {
  theme: Theme
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
}

export const ThemeContext = createContext<ThemeContextValue | null>(null)

type ThemeProviderProps = {
  children: ReactNode
}

export function ThemeProvider({ children }: ThemeProviderProps) {
  const [theme, setThemeState] = useState<Theme>(() => getInitialTheme())

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next)
    writeStoredTheme(next)
  }, [])

  const toggleTheme = useCallback(() => {
    setThemeState((current) => {
      const next: Theme = current === 'light' ? 'dark' : 'light'
      writeStoredTheme(next)
      return next
    })
  }, [])

  useLayoutEffect(() => {
    applyThemeToDocument(theme)
  }, [theme])

  const value = useMemo(
    () => ({ theme, setTheme, toggleTheme }),
    [theme, setTheme, toggleTheme],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}
