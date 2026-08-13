import axios from 'axios'
import { API_BASE } from './config'

let onUnauthorized: (() => void) | null = null

/** Register a handler invoked when a management API call returns 401. */
export function setManagementUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler
}

/** Axios client for cookie-authenticated management API calls. */
export const managementApi = axios.create({
  baseURL: API_BASE,
  timeout: 10_000,
  withCredentials: true,
})

managementApi.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      onUnauthorized?.()
    }
    return Promise.reject(error)
  },
)
