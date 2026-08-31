import { beforeEach, describe, expect, it, vi } from 'vitest'
import { synthesizeSpeech, transcribeSpeech } from '../speech'
import { ApiError } from '../api'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn())
})

describe('transcribeSpeech', () => {
  it('uploads the recording and returns text + language', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(JSON.stringify({ text: 'धारा 103 में क्या है?', language: 'hi' }), {
        status: 200,
      }),
    )
    const result = await transcribeSpeech('sess', new Blob(['x'], { type: 'audio/webm' }), 'auto')
    expect(result.text).toBe('धारा 103 में क्या है?')
    expect(result.language).toBe('hi')
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(String(url)).toContain('/api/v1/speech/transcribe?language=auto')
    expect(init.method).toBe('POST')
    expect(init.headers['X-Session-Id']).toBe('sess')
    expect(init.body).toBeInstanceOf(FormData)
  })

  it('surfaces the structured error envelope', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(JSON.stringify({ error: { code: 'EMPTY_TRANSCRIPTION', message: 'No speech.' } }), {
        status: 422,
      }),
    )
    await expect(transcribeSpeech('sess', new Blob(['x']))).rejects.toMatchObject({
      code: 'EMPTY_TRANSCRIPTION',
    })
  })

  it('maps network failure to a clean error', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockRejectedValue(new TypeError('fetch failed'))
    await expect(transcribeSpeech('sess', new Blob(['x']))).rejects.toBeInstanceOf(ApiError)
  })
})

describe('synthesizeSpeech', () => {
  it('posts text + language and returns audio blob', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response('wav', { status: 200, headers: { 'content-type': 'audio/wav' } }),
    )
    const blob = await synthesizeSpeech('sess', 'Section 103', 'en')
    expect(blob.size).toBeGreaterThan(0)
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(String(url)).toContain('/api/v1/speech/synthesize')
    expect(JSON.parse(init.body)).toEqual({ text: 'Section 103', language: 'en' })
  })

  it('rejects with the structured error when unsupported', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(
        JSON.stringify({ error: { code: 'SPEECH_LANGUAGE_INVALID', message: 'no voice' } }),
        { status: 422 },
      ),
    )
    await expect(synthesizeSpeech('sess', 'text', 'xx')).rejects.toMatchObject({
      code: 'SPEECH_LANGUAGE_INVALID',
    })
  })
})
