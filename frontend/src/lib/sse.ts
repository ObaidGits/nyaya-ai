/**
 * SSE chat client (PRD §6.2, ARCHITECTURE §37).
 *
 * Streams `POST /api/v1/chat` and parses the `event: X / data: {...}` frames
 * into typed callbacks: token, sources, done, error. The returned abort
 * function powers the stop-generation action.
 */

import type { ChatTurnPayload, DoneEvent, ErrorEvent, Source } from '../types'

export interface StreamCallbacks {
  onToken: (token: string) => void
  onSources: (sources: Source[]) => void
  onDone: (meta: DoneEvent) => void
  onError: (error: ErrorEvent) => void
}

const API_BASE = import.meta.env.VITE_API_URL ?? ''

export async function streamChat(
  sessionId: string,
  message: string,
  history: ChatTurnPayload[],
  callbacks: StreamCallbacks,
  signal: AbortSignal,
  language = 'auto',
): Promise<void> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}/api/v1/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Session-Id': sessionId,
      },
      // `language` is the answer-language preference (D-077): "auto" or a
      // supported code. Absent field behaves identically to "auto".
      body: JSON.stringify({ message, history, language }),
      signal,
    })
  } catch (error) {
    if ((error as Error).name === 'AbortError') return
    callbacks.onError({
      code: 'NETWORK_ERROR',
      message: 'Cannot reach the Nyaya service. Check your connection and try again.',
    })
    return
  }

  if (!response.ok || !response.body) {
    let code = 'REQUEST_FAILED'
    let message = `Chat request failed with status ${response.status}.`
    try {
      const body = (await response.json()) as { error?: ErrorEvent }
      if (body.error) {
        code = body.error.code
        message = body.error.message
      }
    } catch {
      // keep generic message
    }
    callbacks.onError({ code, message })
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // SSE frames are separated by a blank line.
      let separator = buffer.indexOf('\n\n')
      while (separator !== -1) {
        const frame = buffer.slice(0, separator)
        buffer = buffer.slice(separator + 2)
        handleFrame(frame, callbacks)
        separator = buffer.indexOf('\n\n')
      }
    }
  } catch (error) {
    // User pressed Stop: the abort rejects reader.read() mid-stream.
    // Treat it as a normal end of stream so the caller can commit the
    // partial answer instead of leaving the composer stuck.
    if ((error as Error).name === 'AbortError') return
    throw error
  }
}

function handleFrame(frame: string, callbacks: StreamCallbacks): void {
  let event = 'message'
  let data = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event: ')) event = line.slice(7).trim()
    else if (line.startsWith('data: ')) data += line.slice(6)
  }
  if (!data) return
  let parsed: unknown
  try {
    parsed = JSON.parse(data)
  } catch {
    return
  }
  switch (event) {
    case 'token':
      callbacks.onToken((parsed as { text: string }).text ?? '')
      break
    case 'sources':
      callbacks.onSources((parsed as { sources: Source[] }).sources ?? [])
      break
    case 'done':
      callbacks.onDone(parsed as DoneEvent)
      break
    case 'error':
      callbacks.onError(parsed as ErrorEvent)
      break
    default:
      break
  }
}
