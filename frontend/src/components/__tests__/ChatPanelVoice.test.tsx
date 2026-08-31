/**
 * ChatPanel + voice integration: the transcript lands in the composer for
 * review — voice never auto-submits and never bypasses the chat pipeline.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatPanel } from '../ChatPanel'
import { ToastHost } from '../Toast'
import type { Conversation } from '../../lib/conversations'

class FakeMediaRecorder {
  state = 'inactive'
  mimeType = 'audio/webm'
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
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

function sseResponse(frames: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(new TextEncoder().encode(frame))
      controller.close()
    },
  })
  return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

function Harness() {
  const [conv, setConv] = useState<Conversation>({
    id: 'c1',
    title: 'Test',
    createdAt: 1,
    messages: [],
  })
  const [language, setLanguage] = useState('auto')
  return (
    <>
    <ChatPanel
      sessionId="sess"
      conversation={conv}
      onMessagesChange={(messages) => setConv({ ...conv, messages })}
      onSelectCitation={() => {}}
      language={language}
      onLanguageChange={setLanguage}
    />
    <ToastHost />
    </>
  )
}

beforeEach(() => {
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
  vi.stubGlobal(
    'navigator',
    Object.assign(navigator, {
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [] } as unknown as MediaStream) },
    }),
  )
  vi.stubGlobal('fetch', vi.fn())
})

describe('ChatPanel voice input', () => {
  it('inserts the transcript into the composer without sending it', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (String(url).includes('/speech/transcribe')) {
        return Promise.resolve(
          new Response(JSON.stringify({ text: 'What does Section 103 say?', language: 'en' }), {
            status: 200,
          }),
        )
      }
      if (String(url).includes('/api/v1/documents')) {
        return Promise.resolve(new Response('[]', { status: 200 }))
      }
      return Promise.resolve(sseResponse(['event: done\ndata: {"refused": false}\n\n']))
    })

    render(<Harness />)

    await userEvent.click(screen.getByRole('button', { name: /speak your question/i }))
    await userEvent.click(screen.getByRole('button', { name: /stop recording/i }))

    const composer = screen.getByLabelText('Ask a legal question') as HTMLTextAreaElement
    await waitFor(() => expect(composer.value).toBe('What does Section 103 say?'))

    // No chat submission happened: the transcript only filled the composer.
    const chatCalls = (fetch as ReturnType<typeof vi.fn>).mock.calls.filter(([url]) =>
      String(url).includes('/api/v1/chat'),
    )
    expect(chatCalls).toHaveLength(0)
  })

  it('lets the user edit the transcript before sending', async () => {
    const seen: { chatBody: { message?: string } | null } = { chatBody: null }
    ;(fetch as ReturnType<typeof vi.fn>).mockImplementation((url: string, init?: RequestInit) => {
      if (String(url).includes('/speech/transcribe')) {
        return Promise.resolve(
          new Response(JSON.stringify({ text: 'What does Section 103 say?', language: 'en' }), {
            status: 200,
          }),
        )
      }
      if (String(url).includes('/api/v1/chat')) {
        seen.chatBody = JSON.parse(String(init?.body)) as { message?: string }
        return Promise.resolve(sseResponse(['event: done\ndata: {"refused": false}\n\n']))
      }
      return Promise.resolve(new Response('[]', { status: 200 }))
    })

    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: /speak your question/i }))
    await userEvent.click(screen.getByRole('button', { name: /stop recording/i }))
    const composer = screen.getByLabelText('Ask a legal question') as HTMLTextAreaElement
    await waitFor(() => expect(composer.value).not.toBe(''))

    await userEvent.type(composer, ' now')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(seen.chatBody).not.toBeNull())
    expect(seen.chatBody?.message).toBe('What does Section 103 say? now')
  })

  it('keeps the composer usable when transcription fails', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (String(url).includes('/speech/transcribe')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              error: { code: 'SPEECH_PROVIDER_UNAVAILABLE', message: 'Provider down.' },
            }),
            { status: 503 },
          ),
        )
      }
      return Promise.resolve(new Response('[]', { status: 200 }))
    })

    render(<Harness />)
    await userEvent.click(screen.getByRole('button', { name: /speak your question/i }))
    await userEvent.click(screen.getByRole('button', { name: /stop recording/i }))
    expect(await screen.findByText(/transcription failed/i)).not.toBeNull()

    const composer = screen.getByLabelText('Ask a legal question') as HTMLTextAreaElement
    await userEvent.type(composer, 'typed question')
    expect(composer.value).toBe('typed question')
  })

  it('shows a Listen button on completed assistant answers in the answer language', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if (String(url).includes('/api/v1/chat')) {
        return Promise.resolve(
          sseResponse([
            'event: token\ndata: {"text": "उत्तर"}\n\n',
            'event: done\ndata: {"refused": false, "language": "hi"}\n\n',
          ]),
        )
      }
      return Promise.resolve(new Response('[]', { status: 200 }))
    })

    render(<Harness />)
    const composer = screen.getByLabelText('Ask a legal question')
    await userEvent.type(composer, 'धारा 103?')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))

    const listen = await screen.findByRole('button', { name: /listen to answer/i })
    expect(listen).not.toBeNull()

    await userEvent.click(listen)
    await waitFor(() =>
      expect(
        (fetch as ReturnType<typeof vi.fn>).mock.calls.some(
          ([url, init]) =>
            String(url).includes('/speech/synthesize') &&
            JSON.parse(String(init?.body)).language === 'hi',
        ),
      ).toBe(true),
    )
  })
})
