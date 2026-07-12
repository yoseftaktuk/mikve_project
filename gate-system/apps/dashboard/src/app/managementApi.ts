import axios from 'axios'
import { API_BASE } from './config'
import { clearManagementToken, getManagementToken } from './managementStorage'

export const managementApi = axios.create({
  baseURL: API_BASE,
  timeout: 10_000,
})

managementApi.interceptors.request.use((config) => {
  const token = getManagementToken()
  if (token) {
    config.headers['X-Management-Token'] = token
  }
  return config
})

managementApi.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401) {
      clearManagementToken()
    }
    return Promise.reject(error)
  },
)
