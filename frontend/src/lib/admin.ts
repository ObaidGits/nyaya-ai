/**
 * Admin console API client (DECISIONS D-080).
 *
 * The admin session is an HttpOnly cookie set by the backend; this client
 * never sees or stores credentials beyond the login POST. Mutating calls
 * carry the `X-Nyaya-Admin` header the backend requires as a CSRF defense.
 * Secrets are only ever written (POST/PUT) — the server returns "set" | ""
 * masks, never values.
 */

import { ApiError, parseError } from './api'

const API_BASE = import.meta.env.VITE_API_URL ?? ''
const ADMIN_MUTATING = { 'X-Nyaya-Admin': '1' } as const

export interface AdminSession {
  enabled: boolean
  authenticated: boolean
}

export interface LlmProviderInfo {
  name: string
  label: string
  requires_api_key: boolean
  /** True only when the provider has no known official API URL. */
  requires_base_url?: boolean
  default_base_url?: string
  default_model?: string
}

export interface AdminSettingsView {
  values: Record<string, string | number | boolean>
  secrets: Record<string, string>
  persisted: string[]
  llm_providers: LlmProviderInfo[]
}

export interface TestResult {
  success: boolean
  latency_ms: number | null
  message: string
}

export interface DependencyStatus {
  status: string
  detail: string
  [key: string]: unknown
}

export interface ResourceStatus {
  status: string
  cpu_cores: number
  total_ram_mb: number | null
  available_ram_mb: number | null
  warnings: string[]
  detail: string
}

export interface SystemStatus {
  backend: DependencyStatus
  resources?: ResourceStatus
  postgres: DependencyStatus
  redis: DependencyStatus
  qdrant: DependencyStatus
  llm: DependencyStatus & { provider?: string; model?: string | null }
  stt: DependencyStatus
  tts: DependencyStatus
  corpus: DependencyStatus & { sha256?: string; act?: string; chunks?: number }
  worker: DependencyStatus
}

export interface CorpusManifest {
  act: string
  act_short: string
  filename: string
  sha256: string
  pages: number
  sections: number
  chunks: number
  ingested_at: string
  artifact_path?: string
}

export interface MemoryInfo {
  architecture: string
  history_max_turns: number
  history_untrusted: boolean
  persistent_server_memory: boolean
}

// 401 and 503 are not thrown here. Callers use them to detect
// "not logged in" and "admin console disabled" without an error path.
async function adminFetch(path: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(`${API_BASE}/api/v1/admin${path}`, init)
  if (!response.ok && response.status !== 401 && response.status !== 503) {
    throw await parseError(response)
  }
  return response
}

export async function fetchSession(): Promise<AdminSession> {
  try {
    const response = await adminFetch('/session')
    return (await response.json()) as AdminSession
  } catch {
    return { enabled: false, authenticated: false }
  }
}

/** Returns null on success (cookie set), or an ApiError for bad creds / disabled. */
export async function adminLogin(
  username: string,
  password: string,
): Promise<ApiError | null> {
  try {
    const response = await adminFetch('/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (response.ok) return null
    throw await parseError(response)
  } catch (error) {
    if (error instanceof ApiError) return error
    return new ApiError(0, 'NETWORK', 'Could not reach the server.')
  }
}

export async function adminLogout(): Promise<void> {
  await adminFetch('/logout', { method: 'POST', headers: ADMIN_MUTATING })
}

export async function fetchSettings(): Promise<AdminSettingsView> {
  const response = await adminFetch('/settings')
  if (!response.ok) throw await parseError(response)
  return response.json()
}

export async function updateSettings(
  values: Record<string, string | number | boolean>,
  secrets: Record<string, string>,
): Promise<AdminSettingsView> {
  const response = await adminFetch('/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...ADMIN_MUTATING },
    body: JSON.stringify({ values, secrets }),
  })
  if (!response.ok) throw await parseError(response)
  return response.json()
}

export async function testConnection(kind: 'llm' | 'stt' | 'tts'): Promise<TestResult> {
  const response = await adminFetch(`/test/${kind}`, {
    method: 'POST',
    headers: ADMIN_MUTATING,
  })
  if (!response.ok) throw await parseError(response)
  return response.json()
}

/** Model ids offered by the configured provider (settings combobox). */
export async function fetchLlmModels(): Promise<{ provider: string; models: string[] }> {
  const response = await adminFetch('/llm/models')
  if (!response.ok) throw await parseError(response)
  return response.json()
}

export async function fetchStatus(): Promise<SystemStatus> {
  const response = await adminFetch('/status')
  if (!response.ok) throw await parseError(response)
  return response.json()
}

export async function fetchCorpus(): Promise<CorpusManifest | DependencyStatus> {
  const response = await adminFetch('/corpus')
  if (!response.ok) throw await parseError(response)
  return response.json()
}

export async function uploadCorpus(
  file: File,
): Promise<{ status: string; corpus: CorpusManifest }> {
  const body = new FormData()
  body.append('file', file)
  const response = await adminFetch('/corpus', {
    method: 'POST',
    headers: ADMIN_MUTATING,
    body,
  })
  if (!response.ok) throw await parseError(response)
  return response.json()
}

export async function fetchMemory(): Promise<MemoryInfo> {
  const response = await adminFetch('/memory')
  if (!response.ok) throw await parseError(response)
  return response.json()
}

export async function updateMemory(historyMaxTurns: number): Promise<void> {
  const response = await adminFetch('/memory', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...ADMIN_MUTATING },
    body: JSON.stringify({ chat_history_max_turns: historyMaxTurns }),
  })
  if (!response.ok) throw await parseError(response)
}
