/**
 * Source drawer: opened by a citation chip click (C-011..C-013).
 * Shows only what the backend sent — exact retrieved text, act/section,
 * page range, source type. No frontend-invented details.
 */

import { useEffect, useRef } from 'react'
import type { Citation } from '../lib/citations'
import { useFocusTrap } from '../hooks/useFocusTrap'
import { useScrollLock } from '../hooks/useScrollLock'
import { BookOpenIcon, FileTextIcon, XIcon } from './icons'

interface SourceDrawerProps {
  citation: Citation | null
  onClose: () => void
}

export function SourceDrawer({ citation, onClose }: SourceDrawerProps) {
  const closeRef = useRef<HTMLButtonElement>(null)
  const trapRef = useFocusTrap<HTMLElement>(Boolean(citation))
  // Focus restore: return focus to the citation chip that opened the drawer.
  const openerRef = useRef<HTMLElement | null>(null)

  useScrollLock(Boolean(citation))

  useEffect(() => {
    if (citation) {
      openerRef.current = document.activeElement as HTMLElement | null
      closeRef.current?.focus()
    } else {
      openerRef.current?.focus?.()
      openerRef.current = null
    }
  }, [citation])

  useEffect(() => {
    if (!citation) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [citation, onClose])

  if (!citation) return null
  const { label, source } = citation
  const isUserDocument = source?.source_type === 'user_document'

  return (
    <div className="fixed inset-0 z-40 flex flex-col justify-end md:flex-row md:justify-end">
      <div
        className="absolute inset-0 animate-fade-in bg-ink-950/40"
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        ref={trapRef}
        role="dialog"
        aria-modal="true"
        aria-label={`Source for ${label}`}
        className="relative flex max-h-[85dvh] w-full animate-sheet-in flex-col rounded-t-2xl bg-white shadow-2xl dark:bg-ink-900 md:h-full md:max-h-none md:max-w-md md:animate-drawer-in md:rounded-none"
      >
        {/* Drag-handle affordance on the mobile sheet (visual only). */}
        <div className="mx-auto mt-2 h-1 w-10 rounded-full bg-ink-300 md:hidden dark:bg-ink-700" aria-hidden="true" />
        <header className="flex items-start justify-between gap-3 border-b border-ink-200 px-5 py-4 dark:border-ink-800">
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-widest text-ink-400 dark:text-ink-500">
              Source evidence
            </p>
            <h2 className="mt-0.5 font-serif text-xl font-semibold leading-tight">{label}</h2>
            <p
              className={`mt-2 inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${
                isUserDocument
                  ? 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-700 dark:bg-amber-950/40 dark:text-amber-300'
                  : 'border-brand-300 bg-brand-50 text-brand-800 dark:border-brand-700 dark:bg-brand-900/40 dark:text-brand-200'
              }`}
            >
              {isUserDocument ? (
                <FileTextIcon className="size-3.5 shrink-0" />
              ) : (
                <BookOpenIcon className="size-3.5 shrink-0" />
              )}
              {source
                ? isUserDocument
                  ? 'Your uploaded document'
                  : 'Statute (bare act)'
                : 'No source on record'}
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="inline-flex size-11 shrink-0 items-center justify-center rounded-lg border border-ink-200 text-ink-600 transition-colors hover:bg-ink-100 hover:text-ink-900 md:size-9 dark:border-ink-700 dark:text-ink-300 dark:hover:bg-ink-800 dark:hover:text-ink-100"
          >
            <XIcon className="size-4.5" />
          </button>
        </header>

        {source ? (
          <div className="scroll-thin flex-1 space-y-5 overflow-y-auto px-5 pb-[max(1rem,env(safe-area-inset-bottom))] pt-4 text-sm md:pb-4">
            <dl className="space-y-2">
              {source.act && (
                <div className="flex gap-3">
                  <dt className="w-20 shrink-0 text-ink-500 dark:text-ink-400">Act</dt>
                  <dd className="min-w-0 font-medium">{source.act}</dd>
                </div>
              )}
              {source.section_number && (
                <div className="flex gap-3">
                  <dt className="w-20 shrink-0 text-ink-500 dark:text-ink-400">Section</dt>
                  <dd className="min-w-0 font-medium">
                    s.{source.section_number}
                    {source.section_title ? ` — ${source.section_title}` : ''}
                  </dd>
                </div>
              )}
              {source.page_start !== undefined && (
                <div className="flex gap-3">
                  <dt className="w-20 shrink-0 text-ink-500 dark:text-ink-400">Pages</dt>
                  <dd className="min-w-0 font-medium">
                    {source.page_start}
                    {source.page_end && source.page_end !== source.page_start
                      ? `–${source.page_end}`
                      : ''}
                  </dd>
                </div>
              )}
              {source.document_id && (
                <div className="flex gap-3">
                  <dt className="w-20 shrink-0 text-ink-500 dark:text-ink-400">Document</dt>
                  <dd className="min-w-0 truncate font-mono text-xs leading-5">{source.document_id}</dd>
                </div>
              )}
            </dl>

            <div>
              <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-widest text-ink-400 dark:text-ink-500">
                Exact retrieved text
              </p>
              <blockquote className="scroll-thin max-h-96 overflow-y-auto whitespace-pre-wrap rounded-lg border border-ink-200 border-l-4 border-l-brand-500 bg-ink-50 p-3.5 font-serif text-[15px] leading-relaxed text-ink-800 dark:border-ink-700 dark:border-l-brand-400 dark:bg-ink-950 dark:text-ink-200">
                {source.text}
              </blockquote>
            </div>

            <p className="border-t border-ink-200 pt-3 text-xs text-ink-500 dark:border-ink-800 dark:text-ink-400">
              Text is shown exactly as retrieved from the source — it is not rewritten or
              summarized.
            </p>
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto px-5 py-4">
            <p className="text-sm text-ink-600 dark:text-ink-300">
              This citation has no matching retrieved source on record. It may have been
              removed from the answer by citation validation.
            </p>
          </div>
        )}
      </aside>
    </div>
  )
}
