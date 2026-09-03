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
const MAX_POLL_FAILURES = 5

/**
 * Upload a PDF and poll its ingestion status until terminal, surfacing the
 * parse → chunk → embed → ready stages (C-016..C-020) and client-safe
 * backend error codes (C-028..C-031).
 */
export function useDocuments(sessionId: string) {
  const [documents, setDocuments] = useState<DocumentListItem[]>([])
  const [statuses, setStatuses] = useState<Record<string, DocumentStatus>>({})
  const [uploadState, setUploadState] = useState<UploadState>({ kind: 'idle' })
  // Per-document timers: concurrent uploads poll independently, and all
  // timers are cleared on unmount so polling never leaks past the hook.
  const pollTimersRef = useRef(new Map<string, number>())
  const mountedRef = useRef(true)

  useEffect(
    () => () => {
      mountedRef.current = false
      for (const timer of pollTimersRef.current.values()) window.clearTimeout(timer)
      pollTimersRef.current.clear()
    },
    [],
  )

  const clearPoll = (documentId: string) => {
    const timer = pollTimersRef.current.get(documentId)
    if (timer !== undefined) window.clearTimeout(timer)
    pollTimersRef.current.delete(documentId)
  }

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
      let failures = 0
      const tick = async () => {
        if (!mountedRef.current) return
        try {
          const status = await getDocumentStatus(sessionId, documentId)
          if (!mountedRef.current) return
          failures = 0
          setStatuses((prev) => ({ ...prev, [documentId]: status }))
          if (TERMINAL.has(status.status)) {
            clearPoll(documentId)
            void refresh()
            return
          }
        } catch (error) {
          if (!mountedRef.current) return
          // 404 (deleted): stop polling quietly.
          if (error instanceof ApiError && error.status === 404) {
            clearPoll(documentId)
            void refresh()
            return
          }
          // Transient 500/network failures: retry a few times, then surface
          // an error status so the UI does not show "Indexing" forever.
          failures += 1
          if (failures >= MAX_POLL_FAILURES) {
            clearPoll(documentId)
            setStatuses((prev) => ({
              ...prev,
              [documentId]: {
                document_id: documentId,
                job_id: '',
                filename: '',
                status: 'failed',
                stages: [],
                error_code: 'POLL_FAILED',
                error_message: 'Could not check the document status. Please retry the upload.',
                page_count: null,
                chunk_count: null,
              },
            }))
            return
          }
        }
        const timer = window.setTimeout(tick, POLL_MS)
        if (!mountedRef.current) {
          window.clearTimeout(timer)
          return
        }
        pollTimersRef.current.set(documentId, timer)
      }
      clearPoll(documentId)
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
      clearPoll(documentId)
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
