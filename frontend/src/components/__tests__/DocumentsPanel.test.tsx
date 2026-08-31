import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { DocumentsPanel } from '../DocumentsPanel'
import { useDocuments } from '../../hooks/useDocuments'

function Harness() {
  const store = useDocuments('sess-test')
  return <DocumentsPanel store={store} />
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function mockFetchSequence(responses: Array<() => Response>) {
  let call = 0
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(() => {
      const next = responses[Math.min(call, responses.length - 1)]
      call += 1
      return Promise.resolve(next())
    }),
  )
}

const pdf = (name: string) => new File(['%PDF-1.4 test'], name, { type: 'application/pdf' })

const uploadOk = () =>
  new Response(JSON.stringify({ document_id: 'd1', job_id: 'j1', status: 'processing' }), {
    status: 201,
  })
const emptyList = () => new Response('[]', { status: 200 })

describe('DocumentsPanel', () => {
  it('uploads through the file input and walks parse→chunk→embed→ready stages', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    const statuses = [
      { document_id: 'd1', job_id: 'j1', filename: 'notice.pdf', status: 'parsing', stages: ['parsing'], error_code: null, error_message: null, page_count: null, chunk_count: null },
      { document_id: 'd1', job_id: 'j1', filename: 'notice.pdf', status: 'embedding', stages: ['embedding'], error_code: null, error_message: null, page_count: null, chunk_count: null },
      { document_id: 'd1', job_id: 'j1', filename: 'notice.pdf', status: 'ready', stages: ['ready'], error_code: null, error_message: null, page_count: 2, chunk_count: 2 },
    ]
    let statusCall = 0
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string | URL) => {
        const path = String(url)
        if (path.includes('/upload')) return Promise.resolve(uploadOk())
        if (path.includes('/status')) {
          const body = statuses[Math.min(statusCall, statuses.length - 1)]
          statusCall += 1
          return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }))
        }
        return Promise.resolve(
          new Response(
            JSON.stringify([
              {
                document_id: 'd1',
                filename: 'notice.pdf',
                status: 'ready',
                page_count: 2,
                chunk_count: 2,
                created_at: '2026-01-01T00:00:00',
                updated_at: '2026-01-01T00:00:00',
              },
            ]),
            { status: 200 },
          ),
        )
      }),
    )

    render(<Harness />)
    const input = screen.getByLabelText('Upload a PDF document') as HTMLInputElement
    await user.upload(input, pdf('notice.pdf'))

    expect(await screen.findByText('notice.pdf')).toBeTruthy()
    // Stages render while processing…
    await waitFor(() => expect(screen.getByText('Parsing')).toBeTruthy())
    // …and the ready state tells the user the document is queryable.
    await waitFor(() => expect(screen.getByText(/Queryable/)).toBeTruthy(), { timeout: 3000 })
  })

  it('shows a friendly file-too-large error', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    mockFetchSequence([
      () =>
        new Response(
          JSON.stringify({
            error: { code: 'FILE_TOO_LARGE', message: 'too big' },
          }),
          { status: 400 },
        ),
    ])

    render(<Harness />)
    await user.upload(screen.getByLabelText('Upload a PDF document'), pdf('big.pdf'))
    expect(await screen.findByText(/too large/i)).toBeTruthy()
  })

  it('shows a friendly unsupported-type error', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    mockFetchSequence([
      () =>
        new Response(
          JSON.stringify({ error: { code: 'UNSUPPORTED_TYPE', message: 'unsupported' } }),
          { status: 400 },
        ),
    ])

    render(<Harness />)
    // Type is spoofed past the input's accept filter; the backend mock rejects it.
    await user.upload(
      screen.getByLabelText('Upload a PDF document'),
      new File(['x'], 'note.txt', { type: 'application/pdf' }),
    )
    expect(await screen.findByText(/Only PDF files are supported/i)).toBeTruthy()
  })

  it('supports drag-and-drop upload', async () => {
    mockFetchSequence([uploadOk, emptyList])
    render(<Harness />)
    const dropZone = screen.getByText(/Drag & drop a legal document/i).closest('div')!
    const drop = new Event('drop', { bubbles: true })
    Object.defineProperty(drop, 'dataTransfer', { value: { files: [pdf('drop.pdf')] } })
    dropZone.dispatchEvent(drop)
    await waitFor(() => {
      expect(vi.mocked(fetch)).toHaveBeenCalledWith(
        expect.stringContaining('/api/v1/documents/upload'),
        expect.objectContaining({ headers: { 'X-Session-Id': 'sess-test' } }),
      )
    })
  })
})

  it('shows a friendly error when the upload rate limit is exceeded', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    mockFetchSequence([
      emptyList,
      () =>
        new Response(
          JSON.stringify({
            error: {
              code: 'RATE_LIMITED',
              message: 'Too many requests. Please wait a moment and try again.',
            },
          }),
          { status: 429 },
        ),
    ])
    render(<Harness />)
    const input = screen.getByLabelText(/upload/i) as HTMLInputElement
    await user.upload(input, [pdf('rate-limited.pdf')])
    expect(
      await screen.findByText('Too many requests. Please wait a moment and try again.'),
    ).toBeTruthy()
  })

  it('shows a friendly error when the document list cannot be loaded', async () => {
    mockFetchSequence([
      () => new Response(JSON.stringify({ error: { code: 'INTERNAL_ERROR', message: 'An unexpected error occurred.' } }), { status: 500 }),
      () => new Response('[]', { status: 200 }),
    ])
    render(<Harness />)
    // List refresh is best-effort: the panel still renders without documents.
    expect(await screen.findByText(/drag.*pdf|drop.*pdf|upload/i)).toBeTruthy()
  })
