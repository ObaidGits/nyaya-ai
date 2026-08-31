/** Upload zone + document list with live parse→chunk→embed→ready stages. */

import { useRef, useState } from 'react'
import type { useDocuments } from '../hooks/useDocuments'
import {
  AlertIcon,
  CheckIcon,
  FileTextIcon,
  TrashIcon,
  UploadIcon,
  XIcon,
} from './icons'

interface DocumentsPanelProps {
  store: ReturnType<typeof useDocuments>
}

const STAGE_LABELS: Record<string, string> = {
  parsing: 'Parsing',
  chunking: 'Chunking',
  embedding: 'Embedding',
  indexing: 'Indexing',
  ready: 'Ready',
}

const STAGES = ['parsing', 'chunking', 'embedding', 'indexing', 'ready']

function friendlyUploadError(code: string, message: string): string {
  switch (code) {
    case 'FILE_TOO_LARGE':
      return 'This file is too large. Please upload a smaller PDF.'
    case 'UNSUPPORTED_TYPE':
      return 'Only PDF files are supported.'
    case 'INVALID_PDF':
    case 'CORRUPT_PDF':
      return 'This file is not a readable PDF.'
    case 'ENCRYPTED_PDF':
      return 'Encrypted PDFs cannot be processed.'
    default:
      return message
  }
}

export function DocumentsPanel({ store }: DocumentsPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const handleFiles = (files: FileList | null) => {
    const file = files?.[0]
    if (file) void store.upload(file)
  }

  return (
    <section aria-label="Your documents" className="space-y-3">
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          handleFiles(e.dataTransfer.files)
        }}
        className={`rounded-xl border-2 border-dashed p-4 text-center transition-all ${
          dragging
            ? 'scale-[1.01] border-brand-500 bg-brand-50 ring-2 ring-brand-500/20 dark:bg-brand-900/30'
            : 'border-ink-300 hover:border-brand-400 dark:border-ink-700 dark:hover:border-brand-600'
        }`}
      >
        <UploadIcon
          className={`mx-auto size-5 transition-colors ${dragging ? 'text-brand-600 dark:text-brand-400' : 'text-ink-400'}`}
        />
        <p className="mt-2 text-sm text-ink-600 dark:text-ink-300">
          Drag &amp; drop a legal document (PDF), or
        </p>
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="mt-2 rounded-lg border border-ink-300 bg-white px-3 py-1.5 text-sm font-medium transition-colors hover:border-brand-400 hover:bg-brand-50 dark:border-ink-700 dark:bg-ink-900 dark:hover:border-brand-600 dark:hover:bg-ink-800"
        >
          Choose a file
        </button>
        <p className="mt-2 text-xs text-ink-400">PDF files only · stays in this session</p>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(e) => {
            handleFiles(e.target.files)
            e.target.value = ''
          }}
          aria-label="Upload a PDF document"
        />
      </div>

      {store.uploadState.kind === 'uploading' && (
        <p className="flex items-center gap-2 text-sm text-ink-500" role="status">
          <span className="size-1.5 animate-pulse rounded-full bg-brand-500" aria-hidden="true" />
          Uploading {store.uploadState.filename}…
        </p>
      )}
      {store.uploadState.kind === 'error' && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200"
        >
          <AlertIcon className="mt-0.5 size-4 shrink-0" />
          <span className="min-w-0 flex-1">
            {friendlyUploadError(store.uploadState.code, store.uploadState.message)}
          </span>
          <button
            type="button"
            onClick={store.dismissError}
            aria-label="Dismiss error"
            className="shrink-0 rounded p-0.5 transition-colors hover:bg-red-100 dark:hover:bg-red-900/60"
          >
            <XIcon className="size-4" />
          </button>
        </div>
      )}

      {store.documents.length > 0 && (
        <ul className="space-y-2" role="list">
          {store.documents.map((doc) => {
            const status = store.statuses[doc.document_id]
            const current = status?.status ?? doc.status
            const stageIndex = STAGES.indexOf(current)
            const failed = current === 'failed'
            return (
              <li
                key={doc.document_id}
                className="rounded-xl border border-ink-200 bg-white p-3 text-sm transition-colors hover:border-brand-300 dark:border-ink-800 dark:bg-ink-900 dark:hover:border-brand-700"
              >
                <div className="flex items-start gap-2.5">
                  <FileTextIcon className="mt-0.5 size-4 shrink-0 text-ink-400" />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="min-w-0 truncate font-medium">{doc.filename}</span>
                      <button
                        type="button"
                        onClick={() => void store.remove(doc.document_id)}
                        aria-label={`Delete ${doc.filename}`}
                        className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-ink-500 transition-colors hover:bg-red-100 hover:text-red-700 dark:hover:bg-red-950/60 dark:hover:text-red-300"
                      >
                        <TrashIcon className="size-3.5" />
                      </button>
                    </div>
                    {failed ? (
                      <p className="mt-1 flex items-start gap-1.5 text-xs text-red-700 dark:text-red-400">
                        <AlertIcon className="mt-0.5 size-3.5 shrink-0" />
                        Failed: {status?.error_message ?? 'processing error'}
                      </p>
                    ) : (
                      <ol className="mt-2 flex flex-wrap items-center gap-x-1 gap-y-1.5" aria-label="Processing stages">
                        {STAGES.map((stage, i) => (
                          <li key={stage} className="flex items-center gap-1">
                            {i > 0 && (
                              <span
                                className={`h-px w-2.5 ${i <= stageIndex ? 'bg-brand-400' : 'bg-ink-200 dark:bg-ink-700'}`}
                                aria-hidden="true"
                              />
                            )}
                            <span
                              className={`flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] transition-colors ${
                                i < stageIndex
                                  ? 'bg-brand-100 text-brand-800 dark:bg-brand-900 dark:text-brand-200'
                                  : i === stageIndex
                                    ? 'bg-brand-700 font-medium text-white dark:bg-brand-400 dark:text-ink-950'
                                    : 'bg-ink-100 text-ink-500 dark:bg-ink-800 dark:text-ink-400'
                              }`}
                            >
                              {i < stageIndex && <CheckIcon className="size-3" />}
                              {STAGE_LABELS[stage]}
                            </span>
                          </li>
                        ))}
                      </ol>
                    )}
                    {current === 'ready' && status && (
                      <p className="mt-1.5 flex items-center gap-1.5 text-xs text-ink-500">
                        <CheckIcon className="size-3.5 shrink-0 text-brand-600 dark:text-brand-400" />
                        Queryable — {status.page_count ?? doc.page_count} page(s),{' '}
                        {status.chunk_count ?? doc.chunk_count} chunk(s). Ask about it in chat.
                      </p>
                    )}
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
