/**
 * Documents bottom sheet (mobile): opened from the composer's paperclip
 * button below `lg`. Same DocumentsPanel content as the desktop rail,
 * presented as a modal bottom sheet with focus trap, Escape, and
 * safe-area padding. Above `lg` the paperclip is hidden and the rail
 * is used instead.
 */

import { useEffect, useRef } from 'react'
import type { useDocuments } from '../hooks/useDocuments'
import { useFocusTrap } from '../hooks/useFocusTrap'
import { useScrollLock } from '../hooks/useScrollLock'
import { DocumentsPanel } from './DocumentsPanel'
import { FileTextIcon, XIcon } from './icons'

interface DocumentsSheetProps {
  open: boolean
  onClose: () => void
  store: ReturnType<typeof useDocuments>
}

export function DocumentsSheet({ open, onClose, store }: DocumentsSheetProps) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const trapRef = useFocusTrap<HTMLElement>(open)

  useScrollLock(open)

  useEffect(() => {
    if (open) closeRef.current?.focus()
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, onClose])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-40 flex flex-col justify-end lg:hidden">
      <div
        className="absolute inset-0 animate-fade-in bg-ink-950/40"
        onClick={onClose}
        aria-hidden="true"
      />
      <section
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-label="Your documents"
        className="relative flex max-h-[85dvh] animate-sheet-in flex-col rounded-t-2xl bg-white shadow-2xl dark:bg-ink-900"
      >
        {/* Drag-handle affordance (visual only). */}
        <div className="mx-auto mt-2 h-1 w-10 rounded-full bg-ink-300 dark:bg-ink-700" aria-hidden="true" />
        <header className="flex items-center justify-between gap-3 px-4 pb-2 pt-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <FileTextIcon className="size-4 text-ink-400" />
            Your documents
          </h2>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close documents"
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-lg text-ink-600 transition-colors hover:bg-ink-100 hover:text-ink-900 dark:text-ink-300 dark:hover:bg-ink-800 dark:hover:text-ink-100"
          >
            <XIcon className="size-4.5" />
          </button>
        </header>
        <div className="scroll-thin flex-1 overflow-y-auto px-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
          <DocumentsPanel store={store} />
        </div>
      </section>
    </div>
  )
}
