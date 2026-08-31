import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useState } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatPanel } from '../ChatPanel'
import type { ChatMessage, Conversation } from '../../lib/conversations'

function sseResponse(frames: string[]): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(new TextEncoder().encode(frame))
      controller.close()
    },
  })
  return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

const frames = [
  'event: token\ndata: {"text": "Murder is punishable "}\n\n',
  'event: token\ndata: {"text": "with imprisonment [BNS s.103(1)]."}\n\n',
  'event: sources\ndata: {"sources": [{"citation": "BNS s.103(1)", "text": "Whoever commits murder", "act": "Bharatiya Nyaya Sanhita, 2023", "section_number": "103(1)", "section_title": "Murder", "page_start": 45, "page_end": 45, "source_type": "statute"}]}\n\n',
  'event: done\ndata: {"confidence": 0.95, "refused": false, "model": "stub", "citations": ["BNS s.103(1)"]}\n\n',
]

function conversation(messages: ChatMessage[] = []): Conversation {
  return { id: 'c1', title: 'Test', createdAt: 1, messages }
}

/** Stateful harness: mirrors how App owns conversation state. */
function Harness({
  initial = conversation(),
  onSelectCitation = () => {},
  language: initialLanguage = 'auto',
}: {
  initial?: Conversation
  onSelectCitation?: (c: { label: string; source?: object }) => void
  language?: string
}) {
  const [conv, setConv] = useState(initial)
  const [language, setLanguage] = useState(initialLanguage)
  return (
    <ChatPanel
      sessionId="sess"
      conversation={conv}
      onMessagesChange={(messages) => setConv({ ...conv, messages })}
      onSelectCitation={onSelectCitation}
      language={language}
      onLanguageChange={setLanguage}
    />
  )
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
    if (String(url).includes('/api/v1/documents')) {
      return Promise.resolve(new Response('[]', { status: 200 }))
    }
    return Promise.resolve(sseResponse(frames))
  }))
})

describe('ChatPanel', () => {
  it('shows example questions in the empty state', () => {
    render(<Harness />)
    expect(screen.getByText('What is the punishment for murder?')).toBeTruthy()
  })

  it('streams a grounded answer progressively with citation chips and opens the source drawer', async () => {
    const user = userEvent.setup()
    const selected: (string | undefined)[] = []
    render(
      <Harness
        onSelectCitation={(c) => selected.push((c.source as { text?: string } | undefined)?.text)}
      />,
    )

    await user.type(screen.getByLabelText('Ask a legal question'), 'What is murder?')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    // Progressive tokens landed in the DOM as they arrived (not a spinner + wall).
    expect(await screen.findByText(/Murder is punishable/)).toBeTruthy()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Show source for BNS s.103(1)' })).toBeTruthy(),
    )

    // Citation chip opens the drawer with backend-provided source details.
    await user.click(screen.getByRole('button', { name: 'Show source for BNS s.103(1)' }))
    expect(selected).toEqual(['Whoever commits murder'])
  })

  it('renders refusal with an explanation and no invented answer', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (String(url).includes('/api/v1/documents')) {
          return Promise.resolve(new Response('[]', { status: 200 }))
        }
        return Promise.resolve(
          sseResponse([
            'event: token\ndata: {"text": "I don\'t know based on the available source material."}\n\n',
            'event: sources\ndata: {"sources": []}\n\n',
            'event: done\ndata: {"confidence": 0.0, "refused": true, "model": null, "citations": []}\n\n',
          ]),
        )
      }),
    )
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(screen.getByLabelText('Ask a legal question'), 'What does the quantum statute say?')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText(/refused to answer/i)).toBeTruthy()
  })

  it('shows a friendly error when the service is unavailable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (String(url).includes('/api/v1/documents')) {
          return Promise.resolve(new Response('[]', { status: 200 }))
        }
        return Promise.resolve(
          sseResponse(['event: error\ndata: {"code": "SERVICE_UNAVAILABLE", "message": "The chat service is currently unavailable."}\n\n']),
        )
      }),
    )
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(screen.getByLabelText('Ask a legal question'), 'hello')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText('The chat service is currently unavailable.')).toBeTruthy()
  })

  it('keeps the persistent disclaimer in the panel chrome', () => {
    render(<Harness />)
    expect(screen.getByText('Not legal advice')).toBeTruthy()
  })

  it('renders the language selector with all options, defaulting to Auto detect', () => {
    render(<Harness />)
    const select = screen.getByLabelText('Answer language') as HTMLSelectElement
    expect(select.value).toBe('auto')
    const labels = Array.from(select.options).map((option) => option.textContent)
    expect(labels).toContain('Auto detect')
    expect(labels).toContain('English')
    expect(labels).toContain('हिन्दी — Hindi')
    expect(labels).toContain('অসমীয়া — Assamese')
    expect(select.options).toHaveLength(13)
  })

  it('switching the language sends the preference with the next message', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.selectOptions(screen.getByLabelText('Answer language'), 'hi')

    await user.type(screen.getByLabelText('Ask a legal question'), 'धारा 103 में क्या प्रावधान है?')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await screen.findByText(/Murder is punishable/)
    const chatCall = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => String(url).includes('/api/v1/chat'))
    expect(chatCall).toBeTruthy()
    const [, init] = chatCall as unknown as [string, RequestInit]
    expect(JSON.parse(String(init.body))).toMatchObject({ language: 'hi' })
  })

  it('keeps existing chat behavior when language stays at auto', async () => {
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(screen.getByLabelText('Ask a legal question'), 'What is murder?')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText(/Murder is punishable/)).toBeTruthy()
    const chatCall = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => String(url).includes('/api/v1/chat'))
    const [, init] = chatCall as unknown as [string, RequestInit]
    expect(JSON.parse(String(init.body))).toMatchObject({ language: 'auto' })
  })
})

  it('shows a friendly error when the rate limit is exceeded', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (String(url).includes('/api/v1/documents')) {
          return Promise.resolve(new Response('[]', { status: 200 }))
        }
        // 429 with the standard error envelope, not an SSE stream.
        return Promise.resolve(
          new Response(
            JSON.stringify({
              error: {
                code: 'RATE_LIMITED',
                message: 'Too many requests. Please wait a moment and try again.',
              },
            }),
            { status: 429 },
          ),
        )
      }),
    )
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(screen.getByLabelText('Ask a legal question'), 'hello')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(
      await screen.findByText('Too many requests. Please wait a moment and try again.'),
    ).toBeTruthy()
  })

  it('shows a friendly error when the backend is unreachable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string) => {
        if (String(url).includes('/api/v1/documents')) {
          return Promise.resolve(new Response('[]', { status: 200 }))
        }
        return Promise.reject(new TypeError('Failed to fetch'))
      }),
    )
    const user = userEvent.setup()
    render(<Harness />)
    await user.type(screen.getByLabelText('Ask a legal question'), 'hello')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    expect(
      await screen.findByText(/Cannot reach the Nyaya service|check your connection/i),
    ).toBeTruthy()
  })
