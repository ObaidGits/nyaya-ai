/**
 * Speech API client (STT/TTS, DECISIONS D-079).
 *
 * Voice is an input/output layer only: transcription returns text for the
 * composer (never auto-submitted), synthesis speaks only the supplied
 * assistant text in the answer's own language. Providers route either to
 * the local server models or to the browser's built-in speech APIs
 * (SPEECH_*_PROVIDER=browser) so a small server holds no speech models.
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

// --- provider routing (browser vs server speech) -----------------------------

export interface SpeechConfig {
  stt_provider: string
  tts_provider: string
}

/** Non-secret speech provider selection so the client can route browser-side. */
export async function fetchSpeechConfig(): Promise<SpeechConfig> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}/api/v1/speech/config`)
  } catch {
    // Routing info unavailable: stay on the server path (legacy behaviour).
    return { stt_provider: 'server', tts_provider: 'server' }
  }
  if (!response.ok) return { stt_provider: 'server', tts_provider: 'server' }
  return response.json()
}

/** Web Speech API availability (browser-side STT). */
export function browserSttSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window)
  )
}

/** speechSynthesis availability (browser-side TTS). */
export function browserTtsSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

/**
 * Transcribe with the browser's Web Speech API (SPEECH_STT_PROVIDER=browser).
 * Resolves with the final transcript; rejects on error or no speech. The
 * transcript is returned to the caller only — never auto-submitted.
 */
export function transcribeInBrowser(language: string): Promise<Transcription> {
  return new Promise((resolve, reject) => {
    const Ctor =
      (window as unknown as { SpeechRecognition?: new () => SpeechRecognitionLike })
        .SpeechRecognition ??
      (window as unknown as { webkitSpeechRecognition?: new () => SpeechRecognitionLike })
        .webkitSpeechRecognition
    if (!Ctor) {
      reject(new Error('This browser does not support browser speech recognition.'))
      return
    }
    const recognition = new Ctor()
    recognition.continuous = false
    recognition.interimResults = false
    if (language && language !== 'auto') recognition.lang = language
    recognition.onresult = (event) => {
      const text = Array.from(event.results)
        .map((result) => result[0]?.transcript ?? '')
        .join(' ')
        .trim()
      if (text) resolve({ text, language: recognition.lang || 'auto' })
      else reject(new Error('No speech was detected in the recording.'))
    }
    recognition.onerror = (event) => {
      reject(new Error(`Browser speech recognition failed: ${String(event.error ?? 'unknown')}.`))
    }
    recognition.onend = () => {
      // onend fires after onresult; nothing to do — promise already settled.
    }
    recognition.start()
  })
}

/** Minimal structural types for the vendor-prefixed Web Speech API. */
interface SpeechRecognitionResultLike {
  0?: { transcript: string }
}
interface SpeechRecognitionLike {
  continuous: boolean
  interimResults: boolean
  lang: string
  start(): void
  onresult: ((event: { results: ArrayLike<SpeechRecognitionResultLike> }) => void) | null
  onerror: ((event: { error?: string }) => void) | null
  onend: (() => void) | null
}

/**
 * Speak text with the browser's speechSynthesis (SPEECH_TTS_PROVIDER=browser).
 * Resolves when playback finishes; rejects on error/unsupported.
 */
export function synthesizeInBrowser(text: string, language: string): Promise<void> {
  return new Promise((resolve, reject) => {
    if (!browserTtsSupported()) {
      reject(new Error('This browser does not support speech synthesis.'))
      return
    }
    const utterance = new SpeechSynthesisUtterance(text)
    if (language && language !== 'auto') utterance.lang = language
    utterance.onend = () => resolve()
    utterance.onerror = () => reject(new Error('Browser speech synthesis failed.'))
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(utterance)
  })
}
