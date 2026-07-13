import axios from 'axios'
import { API_BASE } from './config'

/** Shared Axios client for public gate API calls. */
export const api = axios.create({
  baseURL: API_BASE,
  timeout: 10_000,
})
