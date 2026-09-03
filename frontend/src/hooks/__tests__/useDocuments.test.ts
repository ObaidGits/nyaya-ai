import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useDocuments } from '../useDocuments'

const uploadOk = (id: string) =>
  new Response(JSON.stringify({ document_id: id, job_id: `j-${id}`, status: 'processing' }), {
    status: 201,
  })

const status = (id: string, s: string) =>
  new Response(
    JSON.stringify({
      document_id: id,
      job_id: `j-${id}`,
      filename: 'doc.pdf',
      status: s,
      stages: [s],
      error_code: null,
      error_message: null,
      page_count: null,
      chunk_count: null,
    }),
    { status: 200 },
  )

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function stubFetch(handler: (url: string) => Response | Promise<Response>) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((url: string | URL) => handler(String(url))),
  )
}

describe('useDocuments polling', () => {
  it('stops polling on unmount', async () => {
    let statusCalls = 0
    stubFetch((url) => {
      if (url.includes('/status')) {
        statusCalls += 1
        return status('d1', 'parsing')
      }
      if (url.includes('/upload')) return uploadOk('d1')
      return new Response('[]', { status: 200 })
    })

    const { result, unmount } = renderHook(() => useDocuments('sess'))
    await act(async () => {
      await result.current.upload(new File(['%PDF'], 'doc.pdf', { type: 'application/pdf' }))
    })
    expect(statusCalls).toBeGreaterThan(0)
    const before = statusCalls

    unmount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800 * 10)
    })
    expect(statusCalls).toBe(before)
  })

  it('polls two documents concurrently', async () => {
    const polled = new Set<string>()
    let uploadCount = 0
    stubFetch((url) => {
      const match = url.match(/\/documents\/([^/]+)\/status/)
      if (match) {
        polled.add(match[1])
        return status(match[1], 'ready')
      }
      if (url.includes('/upload')) {
        uploadCount += 1
        return uploadOk(uploadCount === 1 ? 'd1' : 'd2')
      }
      return new Response('[]', { status: 200 })
    })

    const { result } = renderHook(() => useDocuments('sess'))
    await act(async () => {
      await result.current.upload(new File(['%PDF'], 'one.pdf', { type: 'application/pdf' }))
    })
    await act(async () => {
      await result.current.upload(new File(['%PDF'], 'two.pdf', { type: 'application/pdf' }))
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(800 * 5)
    })
    expect(polled).toContain('d1')
    expect(polled).toContain('d2')
  })

  it('surfaces an error status after 5 consecutive poll failures', async () => {
    stubFetch((url) => {
      if (url.includes('/status')) {
        return new Response(JSON.stringify({ error: { code: 'INTERNAL_ERROR', message: 'boom' } }), {
          status: 500,
        })
      }
      if (url.includes('/upload')) return uploadOk('d1')
      return new Response('[]', { status: 200 })
    })

    const { result } = renderHook(() => useDocuments('sess'))
    await act(async () => {
      await result.current.upload(new File(['%PDF'], 'doc.pdf', { type: 'application/pdf' }))
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(800 * 10)
    })
    await waitFor(() => expect(result.current.statuses.d1?.status).toBe('failed'))
    expect(result.current.statuses.d1?.error_code).toBe('POLL_FAILED')
  })
})
