/**
 * Microphone recording hook (STT input layer, DECISIONS D-079).
 *
 * Wraps MediaRecorder with elapsed-time tracking and clean failure states
 * (permission denied, unsupported browser, recording failure). The hook
 * only produces a Blob — transcription and chat submission stay with the
 * caller so voice input never bypasses the user's review of the composer.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export type RecorderState = 'idle' | 'recording'

export interface UseRecorder {
  state: RecorderState
  elapsedSeconds: number
  error: string | null
  /** Live analyser for the mic stream while recording (visualizer input). */
  analyser: AnalyserNode | null
  start: () => Promise<void>
  stop: () => Promise<Blob | null>
  clearError: () => void
}

const MAX_RECORD_SECONDS = 120

export function useRecorder(): UseRecorder {
  const [state, setState] = useState<RecorderState>('idle')
  const [elapsedSeconds, setElapsed] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [analyser, setAnalyser] = useState<AnalyserNode | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const streamRef = useRef<MediaStream | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const timerRef = useRef<number | null>(null)
  const resolveRef = useRef<((blob: Blob | null) => void) | null>(null)

  const teardown = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current)
      timerRef.current = null
    }
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    recorderRef.current = null
    audioContextRef.current?.close().catch(() => undefined)
    audioContextRef.current = null
    setAnalyser(null)
    setState('idle')
  }, [])

  useEffect(() => teardown, [teardown])

  const start = useCallback(async () => {
    setError(null)
    if (
      typeof navigator === 'undefined' ||
      !navigator.mediaDevices?.getUserMedia ||
      typeof window.MediaRecorder === 'undefined'
    ) {
      setError('Audio recording is not supported in this browser.')
      return
    }
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      setError('Microphone permission was denied. Enable it in your browser settings.')
      return
    }
    streamRef.current = stream
    chunksRef.current = []
    // Visualizer tap: AnalyserNode over the same mic stream. Best-effort —
    // visual feedback is optional and must never break recording.
    try {
      const AudioCtx = window.AudioContext
      const context = new AudioCtx()
      const source = context.createMediaStreamSource(stream)
      const node = context.createAnalyser()
      node.fftSize = 256
      source.connect(node)
      audioContextRef.current = context
      setAnalyser(node)
    } catch {
      setAnalyser(null)
    }
    try {
      const recorder = new MediaRecorder(stream)
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || 'audio/webm',
        })
        teardown()
        resolveRef.current?.(blob.size > 0 ? blob : null)
        resolveRef.current = null
      }
      recorderRef.current = recorder
      recorder.start()
      setState('recording')
      setElapsed(0)
      timerRef.current = window.setInterval(() => {
        setElapsed((s) => Math.min(s + 1, MAX_RECORD_SECONDS))
      }, 1000)
    } catch {
      teardown()
      setError('Recording failed to start. Please try again.')
    }
  }, [teardown])

  const stop = useCallback(() => {
    const recorder = recorderRef.current
    if (!recorder || recorder.state === 'inactive') return Promise.resolve(null)
    return new Promise<Blob | null>((resolve) => {
      resolveRef.current = resolve
      recorder.requestData()
      recorder.stop()
    })
  }, [])

  const clearError = useCallback(() => setError(null), [])

  return { state, elapsedSeconds, error, analyser, start, stop, clearError }
}
