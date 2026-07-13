const TOKEN_KEY = 'gate_management_token'

/** Read the management session token from sessionStorage. */
export function getManagementToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY)
}

/** Persist the management session token in sessionStorage. */
export function setManagementToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token)
}

/** Remove the management session token from sessionStorage. */
export function clearManagementToken(): void {
  sessionStorage.removeItem(TOKEN_KEY)
}
