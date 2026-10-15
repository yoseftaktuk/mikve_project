import type { Theme } from './types'

const THEME_KEY = 'gate.theme'

/** Read the saved theme preference from localStorage. */
export function readStoredTheme(): Theme | null {
  const value = localStorage.getItem(THEME_KEY)
  if (value === 'light' || value === 'dark') return value
  return null
}

/** Persist the theme preference to localStorage. */
export function writeStoredTheme(theme: Theme): void {
  localStorage.setItem(THEME_KEY, theme)
}

/** Detect the OS light/dark preference. */
export function getSystemTheme(): Theme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/** Resolve the initial theme from storage or system preference. */
export function getInitialTheme(): Theme {
  return readStoredTheme() ?? getSystemTheme()
}

/** Apply the theme attribute on the document root. */
export function applyThemeToDocument(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme)
}
