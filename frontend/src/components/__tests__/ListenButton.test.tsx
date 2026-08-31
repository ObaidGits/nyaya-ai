/** Listen (TTS) button tests: render, loading, play/stop, error handling. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ListenButton } from '../ListenButton'
import { ToastHost } from '../Toast'

const playMock = vi.fn().mockResolvedValue(undefined)
const pauseMock = vi.fn()

beforeEach(() => {
  // jsdom lacks blob URLs; the component only needs round-trip identity.
  URL.createObjectURL = vi.fn().mockReturnValue('blob:mock')
  URL.revokeObjectURL = vi.fn()
  routeFetch(() =>
    Promise.resolve(new Response('wav', { status: 200, headers: { 'content-type': 'audio/wav' } })),
  )
  vi.stubGlobal(
    'Audio',
    class {
      play = playMock
      pause = pauseMock
      onended: (() => void) | null = null
      onerror: (() => void) | null = null
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      constructor(_src?: string) {}
    },
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

/** /speech/config always resolves a server-provider config (fresh Response); other calls route to `synthesize`. */
function routeFetch(
  synthesize: (url: string, init?: RequestInit) => Promise<Response> | Response,
) {
  ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>) = vi.fn(
    (url: unknown, init?: RequestInit) =>
      String(url).includes('/speech/config')
        ? Promise.resolve(
            new Response(JSON.stringify({ stt_provider: 'server', tts_provider: 'piper' })),
          )
        : Promise.resolve(synthesize(String(url), init)),
  )
}

/** The synthesize call only (skips the config probe). */
function synthesizeCalls(): unknown[][] {
  return (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
    (call) => !String(call[0]).includes('/speech/config'),
  )
}

describe('ListenButton', () => {
  it('renders a Listen button for the assistant answer', () => {
    render(
      <>
        <ListenButton sessionId="sess" text="Section 103." language="en" />
        <ToastHost />
      </>,
    )
    expect((screen.getByRole('button', { name: /listen to answer/i }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('shows a loading state while speech is generated', async () => {
    let release: (value: Response) => void = () => {}
    const gate = new Promise<Response>((resolve) => {
      release = resolve
    })
    routeFetch(() => gate)
    render(
      <>
        <ListenButton sessionId="sess" text="Section 103." language="en" />
        <ToastHost />
      </>,
    )
    void userEvent.click(screen.getByRole('button', { name: /listen to answer/i }))
    const generating = (await screen.findByRole('button', {
      name: /generating speech/i,
    })) as HTMLButtonElement
    expect(generating.disabled).toBe(true)
    release(new Response('wav', { status: 200, headers: { 'content-type': 'audio/wav' } }))
    await waitFor(() => {
      const stopButton = screen.getByRole('button', {
        name: /stop playback/i,
      }) as HTMLButtonElement
      expect(stopButton.disabled).toBe(false)
    })
  })

  it('requests synthesis in the answer language exactly', async () => {
    render(
      <>
        <ListenButton sessionId="sess" text="धारा 103।" language="hi" />
        <ToastHost />
      </>,
    )
    await userEvent.click(screen.getByRole('button', { name: /listen to answer/i }))
    await waitFor(() => expect(synthesizeCalls().length).toBe(1))
    const init = (synthesizeCalls()[0] as unknown[])[1] as RequestInit
    const body = JSON.parse(init.body as string)
    expect(body).toEqual({ text: 'धारा 103।', language: 'hi' })
  })

  it('stops playback on second click without a second request', async () => {
    render(
      <>
        <ListenButton sessionId="sess" text="answer" language="en" />
        <ToastHost />
      </>,
    )
    await userEvent.click(screen.getByRole('button', { name: /listen to answer/i }))
    const stop = await screen.findByRole('button', { name: /stop playback/i })
    await userEvent.click(stop)
    expect(pauseMock).toHaveBeenCalled()
    expect(synthesizeCalls()).toHaveLength(1)
  })

  it('prevents duplicate requests while loading', async () => {
    let release: (value: Response) => void = () => {}
    routeFetch(
      () =>
        new Promise<Response>((resolve) => {
          release = resolve
        }),
    )
    render(
      <>
        <ListenButton sessionId="sess" text="answer" language="en" />
        <ToastHost />
      </>,
    )
    const button = screen.getByRole('button', { name: /listen to answer/i })
    void userEvent.click(button)
    release(new Response('wav', { status: 200, headers: { 'content-type': 'audio/wav' } }))
    await userEvent.click(screen.getByRole('button', { name: /listen to answer|stop playback/i }))
    await waitFor(() => expect(synthesizeCalls()).toHaveLength(1))
  })

  it('shows a clean error and stays usable when TTS fails', async () => {
    routeFetch(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            error: { code: 'SPEECH_PROVIDER_UNAVAILABLE', message: 'TTS down.' },
          }),
          { status: 503 },
        ),
      ),
    )
    render(
      <>
        <ListenButton sessionId="sess" text="answer" language="en" />
        <ToastHost />
      </>,
    )
    await userEvent.click(screen.getByRole('button', { name: /listen to answer/i }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent ?? '').toMatch(/unavailable/i)
    expect((screen.getByRole('button', { name: /listen to answer/i }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('routes TTS to browser speechSynthesis when configured', async () => {
    const spoken: { text: string; lang: string }[] = []
    vi.stubGlobal(
      'SpeechSynthesisUtterance',
      class {
        text: string
        lang: string
        onend: (() => void) | null = null
        onerror: (() => void) | null = null
        constructor(text: string, lang = '') {
          this.text = text
          this.lang = lang
        }
      },
    )
    vi.stubGlobal(
      'speechSynthesis',
      Object.assign(new EventTarget(), {
        speak: (utterance: { text: string; lang: string; onend: (() => void) | null }) => {
          spoken.push({ text: utterance.text, lang: utterance.lang })
          utterance.onend?.()
        },
        cancel: vi.fn(),
      }),
    )
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>) = vi.fn((url: unknown) =>
      Promise.resolve(
        String(url).includes('/speech/config')
          ? new Response(JSON.stringify({ stt_provider: 'server', tts_provider: 'browser' }))
          : new Response('wav', { status: 200 }),
      ),
    )
    render(
      <>
        <ListenButton sessionId="sess" text="Section 103." language="en" />
        <ToastHost />
      </>,
    )
    await userEvent.click(screen.getByRole('button', { name: /listen to answer/i }))
    await waitFor(() => expect(spoken).toHaveLength(1))
    expect(spoken[0]).toEqual({ text: 'Section 103.', lang: 'en' })
    // No server synthesis request was made.
    expect(synthesizeCalls()).toHaveLength(0)
  })
})
