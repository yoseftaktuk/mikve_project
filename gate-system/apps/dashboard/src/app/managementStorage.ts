const TOKEN_KEY = 'gate_management_token'

export function getManagementToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY)
}

export function setManagementToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token)
}

export function clearManagementToken(): void {
  sessionStorage.removeItem(TOKEN_KEY)
}
