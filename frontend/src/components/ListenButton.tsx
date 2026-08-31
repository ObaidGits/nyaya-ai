/**
 * Listen button (TTS front end, DECISIONS D-079).
 *
 * Speaks the assistant answer in the answer's own language (supplied by the
 * chat done event, never guessed). Text and citations are never altered;
 * failures surface a short message and never break the chat.
 */

import { useEffect, useRef, useState } from 'react'
import { SpeakerIcon, StopIcon } from './icons'
import { toast } from '../lib/toast'
import { synthesizeSpeech } from '../lib/speech'

interface ListenButtonProps {
  sessionId: string
  /** Assistant text to read back — unchanged, citations included verbatim. */
  text: string
  /** Language the answer was actually generated in (done event, D-079). */
  language: string
}

export function ListenButton({ sessionId, text, language }: ListenButtonProps) {
  const [loading, setLoading] = useState(false)
  const [playing, setPlaying] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const busyRef = useRef(false)

  useEffect(() => {
    return () => {
      audioRef.current?.pause()
      audioRef.current = null
    }
  }, [])

  const play = async () => {
    if (busyRef.current) return
    busyRef.current = true
    audioRef.current?.pause()
    setLoading(true)
    try {
      const blob = await synthesizeSpeech(sessionId, text, language)
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audio.onended = () => {
        setPlaying(false)
        URL.revokeObjectURL(url)
      }
      audio.onerror = () => {
        setPlaying(false)
        toast.error('Speech playback failed.')
        URL.revokeObjectURL(url)
      }
      audioRef.current = audio
      await audio.play()
      setPlaying(true)
    } catch (err) {
      const detail = err instanceof Error ? err.message : ''
      toast.error(`Speech playback is unavailable.${detail ? ` ${detail}` : ''}`)
    } finally {
      setLoading(false)
      busyRef.current = false
    }
  }

  const stop = () => {
    audioRef.current?.pause()
    setPlaying(false)
  }

  return (
    <span className="inline-flex items-center gap-1">
      <button
        type="button"
        onClick={playing ? stop : () => void play()}
        disabled={loading}
        aria-label={loading ? 'Generating speech' : playing ? 'Stop playback' : 'Listen to answer'}
        className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-ink-500 transition-colors hover:bg-ink-100 hover:text-ink-800 disabled:cursor-not-allowed disabled:opacity-40 dark:hover:bg-ink-800 dark:hover:text-ink-200"
      >
        {loading ? (
          <span className="inline-block size-3.5 animate-pulse rounded-full bg-ink-400" aria-hidden="true" />
        ) : playing ? (
          <StopIcon className="size-3.5" />
        ) : (
          <SpeakerIcon className="size-3.5" />
        )}
        {loading ? 'Loading…' : playing ? 'Stop' : 'Listen'}
      </button>
    </span>
  )
}
