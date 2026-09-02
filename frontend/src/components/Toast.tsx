/**
 * Toast notifications for speech (STT/TTS) errors and status.
 *
 * Speech failures surface as floating alerts instead of inline text that
 * shifts the composer layout. `ToastHost` subscribes to the toast channel
 * (`lib/toast.ts`) and is mounted once near the app root.
 */

import { useEffect, useState } from 'react'
import { subscribe, type ToastItem } from '../lib/toast'

const AUTO_DISMISS_MS = 6000

/** Mount once; renders floating alerts fixed above the composer. */
export function ToastHost() {
  const [items, setItems] = useState<ToastItem[]>([])

  useEffect(
    () =>
      subscribe((item) => {
        setItems((current) => [...current, item])
        window.setTimeout(() => {
          setItems((current) => current.filter((existing) => existing.id !== item.id))
        }, AUTO_DISMISS_MS)
      }),
    [],
  )

  if (items.length === 0) return null

  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed bottom-24 left-1/2 z-50 flex w-full max-w-sm -translate-x-1/2 flex-col gap-2 px-4"
    >
      {items.map((item) => (
        <div
          key={item.id}
          role={item.kind === 'error' ? 'alert' : 'status'}
          className={`pointer-events-auto flex animate-rise items-start justify-between gap-3 rounded-xl border px-4 py-3 text-sm shadow-lg ${
            item.kind === 'error'
              ? 'border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950/90 dark:text-red-200'
              : 'border-ink-300 bg-white text-ink-700 dark:border-ink-700 dark:bg-ink-900 dark:text-ink-200'
          }`}
        >
          <span>{item.message}</span>
          <button
            type="button"
            aria-label="Dismiss notification"
            onClick={() => setItems((current) => current.filter((i) => i.id !== item.id))}
            className="shrink-0 text-xs opacity-60 hover:opacity-100"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}
