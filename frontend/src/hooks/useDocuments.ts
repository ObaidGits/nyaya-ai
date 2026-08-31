/** Session-scoped document uploads + status polling (C-014..C-020). */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ApiError,
  deleteDocument,
  getDocumentStatus,
  listDocuments,
  uploadDocument,
} from '../lib/api'
import type { DocumentListItem, DocumentStatus } from '../types'

export type UploadState =
  | { kind: 'idle' }
  | { kind: 'uploading'; filename: string }
  | { kind: 'error'; code: string; message: string }

const TERMINAL = new Set(['ready', 'failed'])
const POLL_MS = 800

/**
 * Upload a PDF and poll its ingestion status until terminal, surfacing the
 * parse → chunk → embed → ready stages (C-016..C-020) and client-safe
 * backend error codes (C-028..C-031).
 */
export function useDocuments(sessionId: string) {
  const [documents, setDocuments] = useState<DocumentListItem[]>([])
  const [statuses, setStatuses] = useState<Record<string, DocumentStatus>>({})
  const [uploadState, setUploadState] = useState<UploadState>({ kind: 'idle' })
  const pollRef = useRef<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setDocuments(await listDocuments(sessionId))
    } catch {
      // List refresh is best-effort; individual actions surface errors.
    }
  }, [sessionId])

  // Initial list load; post-upload refreshes go through `refresh` below.
  useEffect(() => {
    let active = true
    listDocuments(sessionId)
      .then((items) => {
        if (active) setDocuments(items)
      })
      .catch(() => {
        // List load is best-effort; individual actions surface errors.
      })
    return () => {
      active = false
    }
  }, [sessionId])

  const poll = useCallback(
    (documentId: string) => {
      pollRef.current = documentId
      const tick = async () => {
        try {
          const status = await getDocumentStatus(sessionId, documentId)
          setStatuses((prev) => ({ ...prev, [documentId]: status }))
          if (TERMINAL.has(status.status)) {
            pollRef.current = null
            void refresh()
            return
          }
        } catch (error) {
          // 404 (deleted) or transient failure: stop polling quietly.
          if (error instanceof ApiError && error.status === 404) {
            pollRef.current = null
            void refresh()
            return
          }
        }
        if (pollRef.current === documentId) {
          window.setTimeout(tick, POLL_MS)
        }
      }
      void tick()
    },
    [refresh, sessionId],
  )

  const upload = useCallback(
    async (file: File) => {
      setUploadState({ kind: 'uploading', filename: file.name })
      try {
        const result = await uploadDocument(sessionId, file)
        setUploadState({ kind: 'idle' })
        void refresh()
        poll(result.document_id)
      } catch (error) {
        if (error instanceof ApiError) {
          setUploadState({ kind: 'error', code: error.code, message: error.message })
        } else {
          setUploadState({
            kind: 'error',
            code: 'UPLOAD_FAILED',
            message: 'The upload could not be completed. Please try again.',
          })
        }
      }
    },
    [poll, refresh, sessionId],
  )

  const remove = useCallback(
    async (documentId: string) => {
      try {
        await deleteDocument(sessionId, documentId)
        setDocuments((prev) => prev.filter((d) => d.document_id !== documentId))
      } catch {
        // Deletion failures surface on next list refresh.
      }
    },
    [sessionId],
  )

  return { documents, statuses, uploadState, upload, remove, dismissError: () => setUploadState({ kind: 'idle' }) }
}
