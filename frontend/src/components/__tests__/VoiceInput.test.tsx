/** Voice input (mic) component tests: states, errors, transcript delivery. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { VoiceInput } from '../VoiceInput'
import { ToastHost } from '../Toast'

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = []
  state = 'inactive'
  mimeType = 'audio/webm'
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  constructor() {
    FakeMediaRecorder.instances.push(this)
  }
  start() {
    this.state = 'recording'
  }
  requestData() {}
  stop() {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['audio'], { type: 'audio/webm' }) })
    this.onstop?.()
  }
}

beforeEach(() => {
  FakeMediaRecorder.instances = []
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
  vi.stubGlobal(
    'navigator',
    Object.assign(navigator, {
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [] } as unknown as MediaStream) },
    }),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/** Route /speech/config to a server-provider config; everything else to the responder. */
function stubFetch(respond: () => Response) {
  ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>) = vi.fn((url: unknown) =>
    String(url).includes('/speech/config')
      ? Promise.resolve(
          new Response(JSON.stringify({ stt_provider: 'faster-whisper', tts_provider: 'piper' })),
        )
      : Promise.resolve(respond()),
  )
}

/** Route /speech/config to browser speech; everything else to the responder. */
function stubBrowserFetch(respond: () => Response) {
  ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>) = vi.fn((url: unknown) =>
    String(url).includes('/speech/config')
      ? Promise.resolve(
          new Response(JSON.stringify({ stt_provider: 'browser', tts_provider: 'browser' })),
        )
      : Promise.resolve(respond()),
  )
}

function renderVoice(overrides: Partial<Parameters<typeof VoiceInput>[0]> = {}) {
  const onTranscript = vi.fn()
  render(
    <>
      <VoiceInput sessionId="sess" language="auto" onTranscript={onTranscript} {...overrides} />
      <ToastHost />
    </>,
  )
  return { onTranscript }
}

describe('VoiceInput', () => {
  it('renders an idle mic button with an accessible label', () => {
    renderVoice()
    expect((screen.getByRole('button', { name: /speak your question/i }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('shows recording state with elapsed time and stop control', async () => {
    renderVoice()
    await userEvent.click(screen.getByRole('button', { name: /speak your question/i }))
    expect(
      screen.getByRole('button', { name: /stop recording \(0s\)/i }),
    ).not.toBeNull()
  })

  it('reports permission denial with a clean message', async () => {
    ;(navigator.mediaDevices.getUserMedia as ReturnType<typeof vi.fn>).mockRejectedValue(
      new DOMException('denied', 'NotAllowedError'),
    )
    renderVoice()
    await userEvent.click(screen.getByRole('button', { name: /speak your question/i }))
    expect(await screen.findByText(/microphone permission was denied/i)).not.toBeNull()
  })

  it('reports unsupported browsers instead of failing silently', async () => {
    vi.stubGlobal('MediaRecorder', undefined)
    stubFetch(() => new Response('{}', { status: 200 }))
    renderVoice()
    const button = screen.getByRole('button', { name: /speak your question/i }) as HTMLButtonElement
    // Server-provider config resolved: no MediaRecorder -> disabled.
    await waitFor(() => expect(button.disabled).toBe(true))
  })

  it('routes STT to the browser Web Speech API when configured', async () => {
    const recognition: {
      continuous: boolean
      interimResults: boolean
      lang: string
      start: ReturnType<typeof vi.fn>
      onresult: ((event: { results: ArrayLike<{ 0?: { transcript: string } }> }) => void) | null
      onerror: ((event: { error?: string }) => void) | null
      onend: (() => void) | null
    } = {
      continuous: false,
      interimResults: false,
      lang: '',
      start: vi.fn(),
      onresult: null,
      onerror: null,
      onend: null,
    }
    vi.stubGlobal('SpeechRecognition', function () {
      return recognition
    })
    stubBrowserFetch(() => new Response('{}', { status: 200 }))
    const { onTranscript } = renderVoice()
    await userEvent.click(screen.getByRole('button', { name: /speak your question/i }))
    await waitFor(() => expect(recognition.start).toHaveBeenCalled())
    recognition.lang = 'en'
    recognition.onresult?.({
      results: [{ 0: { transcript: 'What does Section 103 say?' } }],
    })
    await waitFor(() => expect(onTranscript).toHaveBeenCalledWith('What does Section 103 say?'))
  })

  it('shows transcribing state and delivers the transcript', async () => {
    stubFetch(
      () =>
        new Response(JSON.stringify({ text: 'What does Section 103 say?', language: 'en' }), {
          status: 200,
        }),
    )
    const { onTranscript } = renderVoice()
    await userEvent.click(screen.getByRole('button', { name: /speak your question/i }))
    await userEvent.click(screen.getByRole('button', { name: /stop recording/i }))
    await waitFor(() => expect(onTranscript).toHaveBeenCalledWith('What does Section 103 say?'))
  })

  it('shows a clean message on transcription failure', async () => {
    stubFetch(
      () =>
        new Response(
          JSON.stringify({
            error: { code: 'SPEECH_PROVIDER_UNAVAILABLE', message: 'Provider down.' },
          }),
          { status: 503 },
        ),
    )
    renderVoice()
    await userEvent.click(screen.getByRole('button', { name: /speak your question/i }))
    await userEvent.click(screen.getByRole('button', { name: /stop recording/i }))
    expect(await screen.findByText(/transcription failed/i)).not.toBeNull()
  })

  it('reports empty transcription clearly', async () => {
    stubFetch(() => new Response(JSON.stringify({ text: '', language: 'en' }), { status: 200 }))
    renderVoice()
    await userEvent.click(screen.getByRole('button', { name: /speak your question/i }))
    await userEvent.click(screen.getByRole('button', { name: /stop recording/i }))
    expect(await screen.findByText(/no speech was detected/i)).not.toBeNull()
  })
})
