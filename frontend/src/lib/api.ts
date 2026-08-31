/**
 * API client + session identity.
 *
 * Session identity (D-040/D-041, DECISIONS D-027): an anonymous session
 * token held by the client and sent as `X-Session-Id` on every documents
 * call. The token lives in `sessionStorage`, so a refresh keeps the session
 * while a new tab gets a fresh one — views never mix sessions. Ownership is
 * always enforced server-side; the client never sends document ownership
 * data beyond this opaque token.
 */

const SESSION_KEY = 'nyaya.session-id'
const API_BASE = import.meta.env.VITE_API_URL ?? ''

export class ApiError extends Error {
  status: number
  code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
    this.name = 'ApiError'
  }
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string }
}

export async function parseError(response: Response): Promise<ApiError> {
  let code = 'REQUEST_FAILED'
  let message = `Request failed with status ${response.status}.`
  try {
    const body = (await response.json()) as ErrorEnvelope
    if (body.error) {
      code = body.error.code ?? code
      message = body.error.message ?? message
    }
  } catch {
    // Non-JSON error body: keep the generic message.
  }
  return new ApiError(response.status, code, message)
}

function sessionHeader(sessionId: string): HeadersInit {
  return { 'X-Session-Id': sessionId }
}

export function getSessionId(): string {
  let id = sessionStorage.getItem(SESSION_KEY)
  if (!id) {
    const bytes = new Uint8Array(16)
    crypto.getRandomValues(bytes)
    id = `sess-${Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')}`
    sessionStorage.setItem(SESSION_KEY, id)
  }
  return id
}

// --- Health -------------------------------------------------------------------

export type ModelHealth = 'active' | 'unavailable' | 'unknown'

interface ReadinessBody {
  status?: string
  checks?: Record<string, { status?: string; detail?: string | null }>
}

/**
 * Truthful LLM/provider availability from the backend readiness endpoint
 * (`GET /api/v1/health/ready`). Only the backend's real `model` check decides
 * the outcome; anything unreadable reports "unknown" — never a fake healthy
 * state.
 */
export async function fetchModelHealth(): Promise<ModelHealth> {
  try {
    const response = await fetch(`${API_BASE}/api/v1/health/ready`)
    const body = (await response.json()) as ReadinessBody
    const model = body?.checks?.model?.status
    if (model === 'ok') return 'active'
    if (model === 'fail') return 'unavailable'
    return 'unknown'
  } catch {
    return 'unknown'
  }
}

// --- Documents ---------------------------------------------------------------

export async function uploadDocument(
  sessionId: string,
  file: File,
): Promise<{ document_id: string; job_id: string; status: string }> {
  const body = new FormData()
  body.append('file', file)
  const response = await fetch(`${API_BASE}/api/v1/documents/upload`, {
    method: 'POST',
    headers: sessionHeader(sessionId),
    body,
  })
  if (!response.ok) throw await parseError(response)
  return response.json()
}

export async function getDocumentStatus(
  sessionId: string,
  documentId: string,
): Promise<import('../types').DocumentStatus> {
  const response = await fetch(
    `${API_BASE}/api/v1/documents/${documentId}/status`,
    { headers: sessionHeader(sessionId) },
  )
  if (!response.ok) throw await parseError(response)
  return response.json()
}

export async function listDocuments(
  sessionId: string,
): Promise<import('../types').DocumentListItem[]> {
  const response = await fetch(`${API_BASE}/api/v1/documents`, {
    headers: sessionHeader(sessionId),
  })
  if (!response.ok) throw await parseError(response)
  return response.json()
}

export async function deleteDocument(sessionId: string, documentId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/documents/${documentId}`, {
    method: 'DELETE',
    headers: sessionHeader(sessionId),
  })
  if (!response.ok) throw await parseError(response)
}

// --- Forms -------------------------------------------------------------------

export async function listForms(params?: { needs_review?: boolean }): Promise<
  import('../types').FormListItem[]
> {
  const search = new URLSearchParams()
  if (params?.needs_review !== undefined) {
    search.set('needs_review', String(params.needs_review))
  }
  const query = search.toString()
  const response = await fetch(`${API_BASE}/api/v1/forms${query ? `?${query}` : ''}`)
  if (!response.ok) throw await parseError(response)
  return response.json()
}

export async function searchForms(q: string): Promise<import('../types').FormListItem[]> {
  const response = await fetch(
    `${API_BASE}/api/v1/forms/search?q=${encodeURIComponent(q)}`,
  )
  if (!response.ok) throw await parseError(response)
  return response.json()
}

export function formDownloadUrl(formNumber: number): string {
  return `${API_BASE}/api/v1/forms/${formNumber}/download`
}

export const FORMS_ZIP_URL = `${API_BASE}/api/v1/forms/download-all`
