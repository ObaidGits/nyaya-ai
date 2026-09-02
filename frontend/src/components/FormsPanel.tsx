/** Forms panel: search, filter, list, preview, downloads (C-033..C-038). */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, FORMS_ZIP_URL, formDownloadUrl, listForms, searchForms } from '../lib/api'
import { useFocusTrap } from '../hooks/useFocusTrap'
import type { FormListItem } from '../types'
import {
  AlertIcon,
  DownloadIcon,
  EyeIcon,
  FileTextIcon,
  SearchIcon,
  XIcon,
} from './icons'

type Filter = 'all' | 'needs_review' | 'trusted'

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function FormsPanel() {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [forms, setForms] = useState<FormListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<{ form: FormListItem; url: string } | null>(null)
  const searchTimer = useRef<number | null>(null)
  const previewTrapRef = useFocusTrap<HTMLDivElement>(Boolean(preview))

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      setError(null)
      try {
        const result = query.trim()
          ? await searchForms(query.trim())
          : await listForms(
              filter === 'all' ? undefined : { needs_review: filter === 'needs_review' },
            )
        if (!cancelled) setForms(result)
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof ApiError
              ? err.message
              : 'Forms could not be loaded. Please try again.',
          )
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [query, filter])

  // Debounce the search box so typing does not spam the API.
  useEffect(() => {
    return () => {
      if (searchTimer.current) window.clearTimeout(searchTimer.current)
    }
  }, [])

  const onQueryChange = (value: string) => {
    if (searchTimer.current) window.clearTimeout(searchTimer.current)
    searchTimer.current = window.setTimeout(() => setQuery(value), 250)
  }

  const openPreview = async (form: FormListItem) => {
    const response = await fetch(formDownloadUrl(form.form_number))
    if (!response.ok) {
      setError('This form could not be previewed. Try downloading it instead.')
      return
    }
    const blob = await response.blob()
    setPreview({ form, url: URL.createObjectURL(blob) })
  }

  const closePreview = useCallback(() => {
    setPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev.url)
      return null
    })
  }, [])

  useEffect(() => {
    if (!preview) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closePreview()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [preview, closePreview])

  const counts = useMemo(
    () => ({
      review: forms.filter((f) => f.needs_review).length,
    }),
    [forms],
  )

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-ink-200 px-4 py-2.5 dark:border-ink-800">
        <div className="flex items-center gap-2.5">
          <h2 className="font-serif text-base font-semibold leading-tight">Statutory Forms</h2>
          {!loading && !error && forms.length > 0 && (
            <span className="rounded-full bg-ink-100 px-2 py-0.5 text-xs font-medium text-ink-600 dark:bg-ink-800 dark:text-ink-300">
              {forms.length}
            </span>
          )}
        </div>
        <a
          href={FORMS_ZIP_URL}
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand-700 px-3 py-1.5 text-sm font-medium text-white shadow-sm transition-colors hover:bg-brand-800 dark:bg-brand-500 dark:text-ink-950 dark:hover:bg-brand-400"
          download
        >
          <DownloadIcon className="size-4" />
          Download all (ZIP)
        </a>
      </header>

      <div className="flex flex-wrap items-center gap-2 border-b border-ink-200 px-4 py-2.5 dark:border-ink-800">
        <label htmlFor="forms-search" className="sr-only">
          Search forms
        </label>
        <div className="relative min-w-40 flex-1">
          <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-ink-400" />
          <input
            id="forms-search"
            type="search"
            placeholder="Search by title or number…"
            onChange={(e) => onQueryChange(e.target.value)}
            className="w-full rounded-lg border border-ink-300 bg-white py-1.5 pl-9 pr-3 text-sm transition-colors focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20 focus:outline-none dark:border-ink-700 dark:bg-ink-900"
          />
        </div>
        <fieldset className="flex items-center gap-0.5 rounded-full border border-ink-200 bg-ink-100/80 p-0.5 text-xs dark:border-ink-800 dark:bg-ink-900">
          <legend className="sr-only">Filter forms</legend>
          {(
            [
              ['all', 'All'],
              ['trusted', 'Verified'],
              ['needs_review', 'Needs review'],
            ] as const
          ).map(([value, label]) => (
            <label
              key={value}
              className={`cursor-pointer rounded-full px-2.5 py-1 transition-colors ${
                filter === value
                  ? 'bg-white font-medium text-ink-900 shadow-sm dark:bg-ink-800 dark:text-ink-100'
                  : 'text-ink-600 hover:text-ink-900 dark:text-ink-300 dark:hover:text-ink-100'
              }`}
            >
              <input
                type="radio"
                name="forms-filter"
                value={value}
                checked={filter === value}
                onChange={() => setFilter(value)}
                className="sr-only"
              />
              {label}
            </label>
          ))}
        </fieldset>
      </div>

      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-3" role="region" aria-live="polite">
        {error && (
          <p role="alert" className="flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200">
            <AlertIcon className="mt-0.5 size-4 shrink-0" />
            {error}
          </p>
        )}
        {loading && (
          <div className="flex flex-col items-center gap-2 py-10 text-sm text-ink-500">
            <span className="size-1.5 animate-pulse rounded-full bg-brand-500" aria-hidden="true" />
            Loading forms…
          </div>
        )}
        {!loading && !error && forms.length === 0 && (
          <div className="flex flex-col items-center gap-2 py-10 text-center">
            <FileTextIcon className="size-6 text-ink-300 dark:text-ink-600" />
            <p className="text-sm text-ink-500">
              No forms match this search. Try a different term.
            </p>
          </div>
        )}
        <ul className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-3" role="list">
          {forms.map((form) => (
            <li
              key={form.form_number}
              className="flex flex-col rounded-xl border border-ink-200 bg-white p-4 shadow-xs transition-all hover:border-brand-400 hover:shadow-sm dark:border-ink-800 dark:bg-ink-900 dark:hover:border-brand-600"
            >
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-semibold">Form {form.form_number}</p>
                {form.needs_review && (
                  <span className="rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-800 dark:border-amber-700 dark:bg-amber-950/50 dark:text-amber-300">
                    Needs review
                  </span>
                )}
              </div>
              <p className="mt-1 line-clamp-2 flex-1 font-serif text-sm leading-snug text-ink-700 dark:text-ink-300">
                {form.title}
              </p>
              <p className="mt-1.5 text-xs text-ink-500">
                Pages {form.source_page_start}–{form.source_page_end} ·{' '}
                {formatBytes(form.byte_size)}
              </p>
              <div className="mt-3 flex gap-2 text-sm">
                <button
                  type="button"
                  onClick={() => void openPreview(form)}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-ink-300 px-2.5 py-1 transition-colors hover:bg-ink-100 dark:border-ink-700 dark:hover:bg-ink-800"
                >
                  <EyeIcon className="size-3.5" />
                  Preview
                </button>
                <a
                  href={formDownloadUrl(form.form_number)}
                  download={form.output_filename}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-brand-500 px-2.5 py-1 text-brand-700 transition-colors hover:bg-brand-50 dark:border-brand-500 dark:text-brand-300 dark:hover:bg-brand-900/40"
                >
                  <DownloadIcon className="size-3.5" />
                  Download
                </a>
              </div>
            </li>
          ))}
        </ul>
        {!loading && counts.review > 0 && (
          <p className="mt-3 text-xs text-ink-500">
            {counts.review} form(s) flagged for extraction review.
          </p>
        )}
      </div>

      {preview && (
        <div className="fixed inset-0 z-40 flex animate-fade-in items-center justify-center p-4">
          <div className="absolute inset-0 bg-ink-950/50" onClick={closePreview} aria-hidden="true" />
          <div
            ref={previewTrapRef}
            role="dialog"
            aria-modal="true"
            aria-label={`Preview of Form ${preview.form.form_number}`}
            className="relative flex h-full max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-xl bg-white shadow-2xl dark:bg-ink-900"
          >
            <div className="flex items-center justify-between gap-2 border-b border-ink-200 px-4 py-2.5 dark:border-ink-800">
              <p className="min-w-0 truncate text-sm font-medium">
                Form {preview.form.form_number} — {preview.form.title}
              </p>
              <button
                type="button"
                onClick={closePreview}
                aria-label="Close"
                autoFocus
                className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg border border-ink-200 text-ink-600 transition-colors hover:bg-ink-100 hover:text-ink-900 dark:border-ink-700 dark:text-ink-300 dark:hover:bg-ink-800 dark:hover:text-ink-100"
              >
                <XIcon className="size-4.5" />
              </button>
            </div>
            <iframe
              src={preview.url}
              title={`Form ${preview.form.form_number} preview`}
              className="flex-1"
            />
          </div>
        </div>
      )}
    </div>
  )
}
