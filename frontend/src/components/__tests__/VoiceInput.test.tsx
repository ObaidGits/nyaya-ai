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
    renderVoice()
    const button = screen.getByRole('button', { name: /speak your question/i }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
  })

  it('shows transcribing state and delivers the transcript', async () => {
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>) = vi.fn().mockResolvedValue(
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
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>) = vi.fn().mockResolvedValue(
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
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>) = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ text: '', language: 'en' }), { status: 200 }),
    )
    renderVoice()
    await userEvent.click(screen.getByRole('button', { name: /speak your question/i }))
    await userEvent.click(screen.getByRole('button', { name: /stop recording/i }))
    expect(await screen.findByText(/no speech was detected/i)).not.toBeNull()
  })
})
