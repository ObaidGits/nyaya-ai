/** Admin console tests: auth gate, settings render, masking, save, tests, corpus (D-080). */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AdminPanel } from '../AdminPanel'
import { ToastHost } from '../../Toast'

function Harness() {
  return (
    <>
      <AdminPanel onExit={() => {}} />
      <ToastHost />
    </>
  )
}

const SETTINGS_VIEW = {
  values: {
    llm_provider: 'ollama',
    llm_model: 'llama3.1',
    llm_base_url: '',
    llm_timeout_seconds: 120,
    llm_temperature: 0.2,
    llm_num_predict: 1024,
    speech_stt_provider: 'faster-whisper',
    speech_tts_provider: 'parler-tts',
    retrieval_dense_top_k: 10,
    retrieval_sparse_top_k: 20,
    retrieval_confidence_threshold: 0.18,
    rate_limit_chat_per_minute: 30,
    rate_limit_upload_per_minute: 10,
    rate_limit_speech_per_minute: 10,
    chat_history_max_turns: 20,
  },
  secrets: { llm_api_key: 'set', speech_stt_api_key: '', speech_tts_api_key: '' },
  persisted: [],
  llm_providers: [
    { name: 'ollama', label: 'Ollama (local, keyless)', requires_api_key: false },
    { name: 'openai', label: 'OpenAI', requires_api_key: true },
    { name: 'gemini', label: 'Gemini', requires_api_key: true },
  ],
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function errorResponse(status: number, code: string, message: string): Response {
  return new Response(JSON.stringify({ error: { code, message } }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** Route fetch calls by method+path; admin cookie starts authenticated when given. */
function stubAdmin(
  calls: Record<string, () => Response>,
  { authenticated = true }: { authenticated?: boolean } = {},
) {
  const callsMade: { method: string; path: string; body?: string; headers: Headers }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const path = new URL(url, 'http://x').pathname
      const method = (init?.method ?? 'GET').toUpperCase()
      callsMade.push({
        method,
        path,
        body: typeof init?.body === 'string' ? init.body : undefined,
        headers: new Headers(init?.headers),
      })
      const key = `${method} ${path}`
      if (key === 'GET /api/v1/admin/session') {
        return Promise.resolve(
          jsonResponse({ enabled: true, authenticated }),
        )
      }
      const defaults: Record<string, () => Response> = {
        'GET /api/v1/admin/corpus': () =>
          jsonResponse({ status: 'ok', detail: 'active (environment-configured corpus)' }),
        'GET /api/v1/admin/memory': () =>
          jsonResponse({
            architecture: 'client-side conversation history sent per request',
            history_max_turns: 20,
            history_untrusted: true,
            persistent_server_memory: false,
          }),
        'GET /api/v1/admin/status': () =>
          jsonResponse({
            backend: { status: 'ok', version: '1.0.0' },
            postgres: { status: 'ok', detail: 'connected' },
            redis: { status: 'ok', detail: 'connected' },
            qdrant: { status: 'ok', detail: 'HTTP 200' },
            llm: { status: 'ok', provider: 'ollama', model: 'llama3.1', detail: 'reachable' },
            stt: { status: 'configured', provider: 'faster-whisper', model: 'small', detail: '' },
            tts: { status: 'configured', provider: 'parler-tts', model: 'mini', detail: '' },
            corpus: { status: 'ok', detail: 'active' },
            worker: { status: 'not_configured', detail: 'memory mode' },
          }),
      }
      const handler = calls[key] ?? defaults[key]
      if (!handler) return Promise.resolve(errorResponse(404, 'NOT_FOUND', 'no stub'))
      return Promise.resolve(handler())
    }),
  )
  return callsMade
}

beforeEach(() => {
  window.location.hash = '#settings'
})

afterEach(async () => {
  // Flush in-flight panel fetches (corpus/memory/status load on mount) while
  // the stub is still active. Without this, a fetch settling after teardown
  // hits the real undici fetch, which cannot parse relative URLs and fails
  // the run as an unhandled error (flaky, CI run 33390059547).
  await new Promise((resolve) => setTimeout(resolve, 0))
  vi.unstubAllGlobals()
})

describe('AdminPanel auth', () => {
  it('renders login when unauthenticated', async () => {
    stubAdmin({}, { authenticated: false })
    render(<Harness />)
    expect(await screen.findByRole('form', { name: 'Admin sign in' })).toBeTruthy()
    // No settings leak to the login screen.
    expect(screen.queryByLabelText(/Provider/)).toBeNull()
  })

  it('rejects wrong credentials and shows the server message', async () => {
    const calls = stubAdmin(
      { 'POST /api/v1/admin/login': () => errorResponse(401, 'ADMIN_UNAUTHORIZED', 'Invalid credentials.') },
      { authenticated: false },
    )
    render(<Harness />)
    const form = await screen.findByRole('form', { name: 'Admin sign in' })
    await userEvent.type(within(form).getByLabelText('Username'), 'admin')
    await userEvent.type(within(form).getByLabelText('Password'), 'wrong')
    await userEvent.click(within(form).getByRole('button', { name: 'Sign in' }))
    expect((await screen.findByRole('alert')).textContent).toContain('Invalid credentials.')
    expect(calls[calls.length - 1].path).toBe('/api/v1/admin/login')
  })

  it('signs in with valid credentials and loads settings', async () => {
    stubAdmin({
      'POST /api/v1/admin/login': () => jsonResponse({ ok: true }),
      'GET /api/v1/admin/settings': () => jsonResponse(SETTINGS_VIEW),
    }, { authenticated: false })
    render(<Harness />)
    const form = await screen.findByRole('form', { name: 'Admin sign in' })
    await userEvent.type(within(form).getByLabelText('Username'), 'admin')
    await userEvent.type(within(form).getByLabelText('Password'), 'right')
    await userEvent.click(within(form).getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('AI / LLM provider')).toBeTruthy()
  })

  it('shows a disabled state when the server has no admin credentials', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ enabled: false, authenticated: false })),
    )
    render(<Harness />)
    expect(await screen.findByText('Admin console disabled')).toBeTruthy()
  })
})

describe('AdminPanel settings', () => {
  async function renderConsole(extra: Record<string, () => Response> = {}) {
    stubAdmin({
      'GET /api/v1/admin/settings': () => jsonResponse(SETTINGS_VIEW),
      ...extra,
    })
    render(<Harness />)
    await screen.findByText('AI / LLM provider')
  }

  it('renders all sections', async () => {
    await renderConsole()
    for (const title of [
      'AI / LLM provider',
      'Language',
      'Voice (STT / TTS)',
      'Retrieval',
      'Rate limits (requests per minute)',
      'Legal corpus',
    ]) {
      expect(screen.getByText(title)).toBeTruthy()
    }
    // Memory + status panels render after their async fetches resolve.
    expect(await screen.findByText('Conversation memory')).toBeTruthy()
    expect(await screen.findByText('System status')).toBeTruthy()
  })

  it('does not offer switches for grounding, citations, refusal, or injection protection', async () => {
    await renderConsole()
    // No form controls exist for these guarantees — only descriptive text.
    expect(screen.queryAllByLabelText(/citation/i)).toHaveLength(0)
    expect(screen.queryAllByLabelText(/injection/i)).toHaveLength(0)
    expect(screen.queryAllByLabelText(/grounding/i)).toHaveLength(0)
  })

  it('shows provider dropdown from the server list and no API key field for ollama', async () => {
    await renderConsole()
    const provider = screen.getByLabelText('Provider') as HTMLSelectElement
    expect(provider.value).toBe('ollama')
    expect(within(provider).getAllByRole('option').length).toBe(3)
    expect(screen.queryByLabelText('API key')).toBeNull()
  })

  it('reveals API key + base URL fields when switching to a cloud provider', async () => {
    await renderConsole()
    await userEvent.selectOptions(screen.getByLabelText('Provider'), 'openai')
    expect(screen.getByLabelText('Base URL')).toBeTruthy()
    expect(screen.getByLabelText('API key')).toBeTruthy()
  })

  it('masks secrets and never displays stored values', async () => {
    await renderConsole()
    await userEvent.selectOptions(screen.getByLabelText('Provider'), 'openai')
    const key = screen.getByLabelText('API key') as HTMLInputElement
    expect(key.type).toBe('password')
    expect(key.value).toBe('')
    // A key IS configured server-side, shown without revealing it.
    expect(screen.getByText(/A key is configured server-side/)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Show API key' }))
    expect(key.type).toBe('text')
  })

  it('marks unsaved changes, saves, and clears the indicator', async () => {
    const calls = stubAdmin({
      'GET /api/v1/admin/settings': () => jsonResponse(SETTINGS_VIEW),
      'PUT /api/v1/admin/settings': () =>
        jsonResponse({ ...SETTINGS_VIEW, values: { ...SETTINGS_VIEW.values, llm_model: 'llama3.3' } }),
    })
    render(<Harness />)
    await screen.findByText('AI / LLM provider')
    expect(screen.queryByText('Unsaved changes')).toBeNull()
    await userEvent.clear(screen.getByLabelText('Model'))
    await userEvent.type(screen.getByLabelText('Model'), 'llama3.3')
    expect(screen.getByText('Unsaved changes')).toBeTruthy()
    // Reset restores the loaded value.
    await userEvent.click(screen.getByRole('button', { name: 'Reset' }))
    expect((screen.getByLabelText('Model') as HTMLInputElement).value).toBe('llama3.1')
    await userEvent.clear(screen.getByLabelText('Model'))
    await userEvent.type(screen.getByLabelText('Model'), 'llama3.3')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(screen.queryByText('Unsaved changes')).toBeNull())
    const put = calls.find((c) => c.method === 'PUT')!
    expect(put.path).toBe('/api/v1/admin/settings')
    expect(put.headers.get('X-Nyaya-Admin')).toBe('1')
    expect(JSON.parse(put.body!).values.llm_model).toBe('llama3.3')
  })

  it('shows validation errors from the server', async () => {
    stubAdmin({
      'GET /api/v1/admin/settings': () => jsonResponse(SETTINGS_VIEW),
      'PUT /api/v1/admin/settings': () =>
        errorResponse(422, 'SETTINGS_INVALID', 'llm_timeout_seconds must be >= 1'),
    })
    render(<Harness />)
    await screen.findByText('AI / LLM provider')
    await userEvent.clear(screen.getByLabelText('Timeout (seconds)'))
    await userEvent.type(screen.getByLabelText('Timeout (seconds)'), '5')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect((await screen.findByRole('alert')).textContent).toContain('llm_timeout_seconds')
  })

  it('runs a connection test and shows latency', async () => {
    stubAdmin({
      'GET /api/v1/admin/settings': () => jsonResponse(SETTINGS_VIEW),
      'POST /api/v1/admin/test/llm': () =>
        jsonResponse({ success: true, latency_ms: 42, message: 'ollama / llama3.1: reachable.' }),
    })
    render(<Harness />)
    await screen.findByText('AI / LLM provider')
    await userEvent.click(screen.getByRole('button', { name: 'Test LLM connection' }))
    expect(await screen.findByText(/reachable\. \(42 ms\)/)).toBeTruthy()
  })
})

describe('AdminPanel corpus', () => {
  const MANIFEST = {
    act: 'Bharatiya Nyaya Sanhita, 2023',
    act_short: 'BNS',
    filename: 'bns.pdf',
    sha256: 'deadbeef',
    pages: 200,
    sections: 358,
    chunks: 900,
    ingested_at: '2026-08-31T00:00:00+00:00',
  }

  async function renderCorpus(extra: Record<string, () => Response> = {}) {
    stubAdmin({
      'GET /api/v1/admin/settings': () => jsonResponse(SETTINGS_VIEW),
      'GET /api/v1/admin/corpus': () => jsonResponse({ status: 'ok', ...MANIFEST }),
      ...extra,
    })
    render(<Harness />)
    await screen.findByText('Legal corpus')
  }

  it('shows the active corpus manifest', async () => {
    await renderCorpus()
    expect(await screen.findByText('Bharatiya Nyaya Sanhita, 2023')).toBeTruthy()
    expect(screen.getByText('deadbeef')).toBeTruthy()
  })

  it('requires confirmation before replacement and reports rejection', async () => {
    stubAdmin({
      'GET /api/v1/admin/settings': () => jsonResponse(SETTINGS_VIEW),
      'GET /api/v1/admin/corpus': () => jsonResponse({ status: 'ok', ...MANIFEST }),
      'POST /api/v1/admin/corpus': () =>
        errorResponse(422, 'CORPUS_REJECTED', 'source does not match expected corpus'),
    })
    render(<Harness />)
    await screen.findByText('Legal corpus')
    const file = new File(['%PDF-fake'], 'bnss.pdf', { type: 'application/pdf' })
    await userEvent.upload(screen.getByLabelText('Replace corpus (Gazette PDF)'), file)
    await userEvent.click(screen.getByRole('button', { name: 'Replace corpus' }))
    const dialog = screen.getByRole('alertdialog')
    await userEvent.click(within(dialog).getByRole('button', { name: 'Yes, replace' }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    // Old manifest still shown after rejection.
    expect(screen.getByText('deadbeef')).toBeTruthy()
  })
})

describe('AdminPanel status', () => {
  it('renders truthful dependency states', async () => {
    stubAdmin({
      'GET /api/v1/admin/settings': () => jsonResponse(SETTINGS_VIEW),
      'GET /api/v1/admin/status': () =>
        jsonResponse({
          backend: { status: 'ok', version: '1.0.0' },
          postgres: { status: 'unavailable', detail: 'unreachable' },
          redis: { status: 'ok', detail: 'connected' },
          qdrant: { status: 'ok', detail: 'HTTP 200' },
          llm: { status: 'ok', provider: 'ollama', model: 'llama3.1', detail: 'reachable' },
          stt: { status: 'configured', provider: 'faster-whisper', model: 'small' },
          tts: { status: 'configured', provider: 'parler-tts', model: 'mini' },
          corpus: { status: 'ok', detail: 'active', act: 'Bharatiya Nyaya Sanhita, 2023' },
          worker: { status: 'not_configured', detail: 'memory mode' },
        }),
    })
    render(<Harness />)
    await screen.findByText('System status')
    expect(screen.getByText('unavailable')).toBeTruthy()
    expect(screen.getByText('not configured')).toBeTruthy()
    expect(screen.getAllByText(/Bharatiya Nyaya Sanhita, 2023/).length).toBeGreaterThan(1)
  })
})

describe('AdminPanel memory', () => {
  it('documents client-side memory and saves the history cap', async () => {
    const calls = stubAdmin({
      'GET /api/v1/admin/settings': () => jsonResponse(SETTINGS_VIEW),
      'GET /api/v1/admin/memory': () =>
        jsonResponse({
          architecture: 'client-side conversation history sent per request',
          history_max_turns: 20,
          history_untrusted: true,
          persistent_server_memory: false,
        }),
      'PUT /api/v1/admin/memory': () =>
        jsonResponse({ chat_history_max_turns: 10, note: '' }),
    })
    render(<Harness />)
    await screen.findByText('Conversation memory')
    expect(screen.getByText(/never a source of legal authority/)).toBeTruthy()
    await userEvent.clear(screen.getByLabelText('History window (turns)'))
    await userEvent.type(screen.getByLabelText('History window (turns)'), '10')
    await userEvent.click(screen.getByRole('button', { name: /^Save$/ }))
    await screen.findByText('Saved.')
    const put = calls.find((c) => c.method === 'PUT' && c.path.endsWith('/memory'))!
    expect(put.headers.get('X-Nyaya-Admin')).toBe('1')
    expect(JSON.parse(put.body!).chat_history_max_turns).toBe(10)
  })
})
