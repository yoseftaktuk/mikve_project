import type { Theme } from './types'

const THEME_KEY = 'gate.theme'

export function readStoredTheme(): Theme | null {
  const value = localStorage.getItem(THEME_KEY)
  if (value === 'light' || value === 'dark') return value
  return null
}

export function writeStoredTheme(theme: Theme): void {
  localStorage.setItem(THEME_KEY, theme)
}

export function getSystemTheme(): Theme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function getInitialTheme(): Theme {
  return readStoredTheme() ?? getSystemTheme()
}

export function applyThemeToDocument(theme: Theme): void {
  document.documentElement.setAttribute('data-theme', theme)
}
