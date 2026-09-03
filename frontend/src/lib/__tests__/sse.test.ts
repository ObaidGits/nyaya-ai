import { afterEach, describe, expect, it, vi } from 'vitest'
import { streamChat } from '../sse'

function sseResponse(frames: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(new TextEncoder().encode(frame))
      controller.close()
    },
  })
  return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

function callbacks() {
  return {
    onToken: vi.fn(),
    onSources: vi.fn(),
    onDone: vi.fn(),
    onError: vi.fn(),
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('streamChat', () => {
  it('parses token, sources, and done events in order', async () => {
    const cb = callbacks()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: token\ndata: {"text": "Murder is "}\n\n',
          'event: token\ndata: {"text": "punishable"}\n\n',
          'event: sources\ndata: {"sources": [{"citation": "BNS s.103", "text": "…"}]}\n\n',
          'event: done\ndata: {"confidence": 0.9, "refused": false, "model": "stub", "citations": []}\n\n',
        ]),
      ),
    )

    await streamChat('sess', 'hi', [], cb, new AbortController().signal)

    expect(cb.onToken).toHaveBeenNthCalledWith(1, 'Murder is ')
    expect(cb.onToken).toHaveBeenNthCalledWith(2, 'punishable')
    expect(cb.onSources).toHaveBeenCalledWith([
      { citation: 'BNS s.103', text: '…' },
    ])
    expect(cb.onDone).toHaveBeenCalledWith({ confidence: 0.9, refused: false, model: 'stub', citations: [] })
    expect(cb.onError).not.toHaveBeenCalled()
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      '/api/v1/chat',
      expect.objectContaining({
        headers: expect.objectContaining({ 'X-Session-Id': 'sess' }),
      }),
    )
  })

  it('parses frames with CRLF line endings (Windows-style transport)', async () => {
    const cb = callbacks()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: token\r\ndata: {"text": "hello"}\r\n\r\n',
          'event: done\r\ndata: {"confidence": 0.9, "refused": false, "model": "stub", "citations": []}\r\n\r\n',
        ]),
      ),
    )
    await streamChat('sess', 'hi', [], cb, new AbortController().signal)
    expect(cb.onToken).toHaveBeenCalledWith('hello')
    expect(cb.onDone).toHaveBeenCalledWith({ confidence: 0.9, refused: false, model: 'stub', citations: [] })
  })

  it('handles split frames across chunk boundaries', async () => {
    const cb = callbacks()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: tok',
          'en\ndata: {"text": "hello"}\n',
          '\n',
        ]),
      ),
    )
    await streamChat('sess', 'hi', [], cb, new AbortController().signal)
    expect(cb.onToken).toHaveBeenCalledWith('hello')
  })

  it('surfaces error envelopes without internals', async () => {
    const cb = callbacks()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: { code: 'SERVICE_UNAVAILABLE', message: 'down' } }), {
          status: 503,
        }),
      ),
    )
    await streamChat('sess', 'hi', [], cb, new AbortController().signal)
    expect(cb.onError).toHaveBeenCalledWith({ code: 'SERVICE_UNAVAILABLE', message: 'down' })
    expect(cb.onToken).not.toHaveBeenCalled()
  })

  it('reports network failures as a friendly error', async () => {
    const cb = callbacks()
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('fetch failed')))
    await streamChat('sess', 'hi', [], cb, new AbortController().signal)
    expect(cb.onError).toHaveBeenCalledWith(
      expect.objectContaining({ code: 'NETWORK_ERROR' }),
    )
  })

  it('stays quiet on abort (stop generation)', async () => {
    const cb = callbacks()
    const controller = new AbortController()
    controller.abort()
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('aborted', 'AbortError')))
    await streamChat('sess', 'hi', [], cb, controller.signal)
    expect(cb.onError).not.toHaveBeenCalled()
  })

  it('sends the language preference in the request body', async () => {
    const cb = callbacks()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse(['event: done\ndata: {"confidence": 0.9, "refused": false, "model": "stub", "citations": []}\n\n']),
      ),
    )
    await streamChat('sess', 'धारा 103', [], cb, new AbortController().signal, 'hi')
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      '/api/v1/chat',
      expect.objectContaining({
        body: JSON.stringify({ message: 'धारा 103', history: [], language: 'hi' }),
      }),
    )
  })

  it('defaults the language field to auto', async () => {
    const cb = callbacks()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sseResponse(['event: done\ndata: {"confidence": 0.9, "refused": false, "model": "stub", "citations": []}\n\n']),
      ),
    )
    await streamChat('sess', 'hi', [], cb, new AbortController().signal)
    expect(vi.mocked(fetch)).toHaveBeenCalledWith(
      '/api/v1/chat',
      expect.objectContaining({
        body: JSON.stringify({ message: 'hi', history: [], language: 'auto' }),
      }),
    )
  })
})
