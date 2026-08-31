/**
 * Speech API client (STT/TTS, DECISIONS D-079).
 *
 * Voice is an input/output layer only: transcription returns text for the
 * composer (never auto-submitted), synthesis speaks only the supplied
 * assistant text in the answer's own language. No paid or cloud speech
 * services — the backend routes to local providers.
 */

import { ApiError, parseError } from './api'

const API_BASE = import.meta.env.VITE_API_URL ?? ''

export interface Transcription {
  text: string
  language: string
}

/** Upload one recorded clip for transcription. Language "auto" detects. */
export async function transcribeSpeech(
  sessionId: string,
  audio: Blob,
  language = 'auto',
): Promise<Transcription> {
  const body = new FormData()
  body.append('file', audio, 'recording.webm')
  let response: Response
  try {
    response = await fetch(
      `${API_BASE}/api/v1/speech/transcribe?language=${encodeURIComponent(language)}`,
      { method: 'POST', headers: { 'X-Session-Id': sessionId }, body },
    )
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', 'Cannot reach the speech service. Check your connection.')
  }
  if (!response.ok) throw await parseError(response)
  return response.json()
}

/** Synthesize assistant text in a concrete language; returns playable audio. */
export async function synthesizeSpeech(
  sessionId: string,
  text: string,
  language: string,
): Promise<Blob> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}/api/v1/speech/synthesize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Session-Id': sessionId },
      body: JSON.stringify({ text, language }),
    })
  } catch {
    throw new ApiError(0, 'NETWORK_ERROR', 'Cannot reach the speech service. Check your connection.')
  }
  if (!response.ok) throw await parseError(response)
  return response.blob()
}

/** MediaRecorder availability (browser support check for the mic button). */
export function recordingSupported(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof window.MediaRecorder !== 'undefined'
  )
}
