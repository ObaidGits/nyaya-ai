/**
 * Voice input button (STT front end, DECISIONS D-079).
 *
 * Mic click → record (live equalizer + elapsed timer + stop) → upload →
 * "Transcribing…" → the transcript is inserted into the composer and the
 * user reviews/edits before sending. Never auto-submits: voice input goes
 * through the exact same chat pipeline as typed text. Status and errors
 * surface as floating toasts so the composer layout never shifts.
 */

import { useEffect, useState } from 'react'
import { toast } from '../lib/toast'
import { MicIcon, StopIcon } from './icons'
import { RecordingBars } from './RecordingBars'
import { useRecorder } from '../hooks/useRecorder'
import { recordingSupported, transcribeSpeech } from '../lib/speech'

interface VoiceInputProps {
  sessionId: string
  /** Selected language preference; "auto" lets the backend detect. */
  language: string
  onTranscript: (text: string) => void
  disabled?: boolean
}

export function VoiceInput({ sessionId, language, onTranscript, disabled }: VoiceInputProps) {
  const recorder = useRecorder()
  const [transcribing, setTranscribing] = useState(false)
  const supported = recordingSupported()

  // Recorder failures (permission denied, unsupported browser, start error)
  // surface as toasts — never inline text that shifts the composer.
  useEffect(() => {
    if (recorder.error) toast.error(recorder.error)
  }, [recorder.error])

  const handleClick = async () => {
    if (transcribing) return
    if (recorder.state === 'recording') {
      setTranscribing(true)
      toast.info('Transcribing…')
      const blob = await recorder.stop()
      if (!blob) {
        setTranscribing(false)
        toast.error('No speech was captured. Please try again.')
        return
      }
      try {
        const result = await transcribeSpeech(sessionId, blob, language)
        if (!result.text.trim()) {
          toast.error('No speech was detected in the recording.')
        } else {
          onTranscript(result.text)
        }
      } catch (error) {
        const text = error instanceof Error ? error.message : 'Transcription failed.'
        toast.error(
          text === 'No speech was detected in the audio.'
            ? 'No speech was detected in the recording.'
            : `Transcription failed: ${text}`,
        )
      } finally {
        setTranscribing(false)
      }
      return
    }
    await recorder.start()
  }

  const recording = recorder.state === 'recording'
  const label = transcribing
    ? 'Transcribing…'
    : recording
      ? `Stop recording (${recorder.elapsedSeconds}s)`
      : 'Speak your question'
  const disabledNow = disabled || transcribing || !supported

  return (
    <div className="flex h-[44px] items-center gap-2">
      {recording && <RecordingBars analyser={recorder.analyser} />}
      {recording && (
        <span className="text-xs tabular-nums text-red-600 dark:text-red-400">
          {recorder.elapsedSeconds}s
        </span>
      )}
      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={disabledNow}
        aria-label={label}
        aria-pressed={recording}
        title={supported ? undefined : 'Audio recording is not supported in this browser.'}
        className={`inline-flex size-[44px] shrink-0 items-center justify-center rounded-xl border transition-colors ${
          recording
            ? 'animate-pulse border-red-400 bg-red-50 text-red-700 dark:border-red-700 dark:bg-red-950/40 dark:text-red-300'
            : 'border-ink-300 text-ink-600 hover:bg-ink-100 dark:border-ink-700 dark:text-ink-300 dark:hover:bg-ink-800'
        } disabled:cursor-not-allowed disabled:opacity-40`}
      >
        {recording ? <StopIcon className="size-4" /> : <MicIcon className="size-4.5" />}
      </button>
    </div>
  )
}
