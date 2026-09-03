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
  value_sources: { llm_provider: 'env' },
  persisted: [],
  llm_providers: [
    {
      name: 'ollama',
      label: 'Ollama (local, keyless)',
      requires_api_key: false,
      requires_base_url: false,
      default_base_url: 'http://localhost:11434',
      default_model: 'llama3.1:8b',
    },
    {
      name: 'openai',
      label: 'OpenAI',
      requires_api_key: true,
      requires_base_url: false,
      default_base_url: 'https://api.openai.com/v1',
      default_model: 'gpt-4o-mini',
    },
    {
      name: 'openai-compatible',
      label: 'OpenAI-compatible',
      requires_api_key: true,
      requires_base_url: true,
      default_base_url: '',
      default_model: '',
    },
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
        'GET /api/v1/admin/providers': () =>
          jsonResponse({
            pools: {
              llm: { entries: [], default_entry_id: null, strategy: 'priority', mode: 'environment' },
              stt: { entries: [], default_entry_id: null, strategy: 'priority', mode: 'environment' },
              tts: { entries: [], default_entry_id: null, strategy: 'priority', mode: 'environment' },
            },
            registered_llm_providers: ['ollama', 'gemini', 'groq'],
            speech_stt_providers: ['faster-whisper', 'browser'],
            speech_tts_providers: ['piper', 'browser'],
            env_fallback: {
              llm_provider: 'ollama',
              llm_model: 'llama3.1',
              speech_stt_provider: 'faster-whisper',
              speech_tts_provider: 'piper',
            },
          }),
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

  it('reveals the API key for fixed-URL cloud providers but hides the base URL field', async () => {
    await renderConsole()
    await userEvent.selectOptions(screen.getByLabelText('Provider'), 'openai')
    expect(screen.getByLabelText('API key')).toBeTruthy()
    // OpenAI has an official API URL — the field stays hidden behind an
    // explicit override instead of being asked for.
    expect(screen.queryByLabelText('Base URL')).toBeNull()
    expect(
      screen.getByText(/Use a custom API URL instead of the provider default/),
    ).toBeTruthy()
  })

  it('asks for the base URL only for providers without a fixed one', async () => {
    await renderConsole()
    await userEvent.selectOptions(screen.getByLabelText('Provider'), 'openai-compatible')
    // Label carries a required marker ("Base URL *").
    expect(screen.getByLabelText(/Base URL/)).toBeTruthy()
    expect(screen.getByLabelText('API key')).toBeTruthy()
  })

  it('loads the provider model list into a datalist and still accepts typed ids', async () => {
    const calls = stubAdmin({
      'GET /api/v1/admin/settings': () => jsonResponse(SETTINGS_VIEW),
      'POST /api/v1/admin/llm/models': () =>
        jsonResponse({ provider: 'ollama', models: ['llama3.1:8b', 'qwen2.5:7b'] }),
    })
    render(<Harness />)
    await screen.findByText('AI / LLM provider')
    const input = screen.getByLabelText('Model') as HTMLInputElement
    await userEvent.click(screen.getByRole('button', { name: 'Load models' }))
    expect(await screen.findByText('2 models loaded from ollama.')).toBeTruthy()
    // The list opens immediately after loading (datalist was never shown
    // by some browsers — this is a real dropdown now).
    const list = await screen.findByRole('listbox', { name: 'Available models' })
    const options = within(list).getAllByRole('option')
    expect(options.map((o) => o.textContent)).toEqual(['llama3.1:8b', 'qwen2.5:7b'])
    // Picking a model fills the input and closes the list.
    await userEvent.click(within(list).getByText('qwen2.5:7b'))
    expect(input.value).toBe('qwen2.5:7b')
    expect(screen.queryByRole('listbox')).toBeNull()
    // The request carries the form's DRAFT config (provider + typed key), not
    // just the saved state.
    const load = calls.find((c) => c.path === '/api/v1/admin/llm/models')!
    const body = JSON.parse(load.body!)
    expect(body.provider).toBe('ollama')
    expect(load.headers.get('X-Nyaya-Admin')).toBe('1')
    // Typing an arbitrary model id is still allowed.
    await userEvent.clear(input)
    await userEvent.type(input, 'llama3.1')
    expect(input.value).toBe('llama3.1')
  })

  it('notes that an env key is only the bootstrap default (console key wins)', async () => {
    stubAdmin({
      'GET /api/v1/admin/settings': () =>
        jsonResponse({
          ...SETTINGS_VIEW,
          secret_sources: { llm_api_key: 'env', speech_stt_api_key: '', speech_tts_api_key: '' },
        }),
    })
    render(<Harness />)
    await screen.findByText('AI / LLM provider')
    await userEvent.selectOptions(screen.getByLabelText('Provider'), 'openai')
    expect(
      screen.getByText(/bootstrap default — saving a key here overrides it/),
    ).toBeTruthy()
  })

  it('does not repeat the console-override hint under every field', async () => {
    stubAdmin({
      'GET /api/v1/admin/settings': () =>
        jsonResponse({
          ...SETTINGS_VIEW,
          value_sources: { ...SETTINGS_VIEW.value_sources, llm_timeout_seconds: 'console' },
        }),
    })
    render(<Harness />)
    await screen.findByText('AI / LLM provider')
    // The per-field ENV-override helper text is gone (console values still
    // override the environment — silently, without the repeated notice).
    expect(screen.queryByText(/Saved in the admin console/)).toBeNull()
    expect(screen.queryByText(/overrides the environment/)).toBeNull()
  })

  it('masks secrets and never displays stored values', async () => {
    await renderConsole()
    await userEvent.selectOptions(screen.getByLabelText('Provider'), 'openai')
    const key = screen.getByLabelText('API key') as HTMLInputElement
    expect(key.type).toBe('password')
    expect(key.value).toBe('')
    // A key IS configured server-side, shown without revealing it.
    expect(screen.getByText(/A key is saved in the admin console/)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Show API key' }))
    expect(key.type).toBe('text')
  })

  it('marks unsaved changes, saves, and shows a success banner naming the active provider', async () => {
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
    // Success banner names the active provider/model and stays until dismissed.
    const banner = await screen.findByRole('status', { name: 'Save result' })
    expect(banner.textContent).toContain('Settings saved — ollama / llama3.3 is now the active provider.')
    const put = calls.find((c) => c.method === 'PUT')!
    expect(put.path).toBe('/api/v1/admin/settings')
    expect(put.headers.get('X-Nyaya-Admin')).toBe('1')
    const body = JSON.parse(put.body!)
    expect(body.values.llm_model).toBe('llama3.3')
    expect(body.clear_secrets).toEqual([])
    expect(body.force).toBe(false)
    await userEvent.click(within(banner).getByRole('button', { name: 'Dismiss message' }))
    expect(screen.queryByRole('status', { name: 'Save result' })).toBeNull()
  })

  it('offers "Save anyway" after verification fails, and sends force on retry', async () => {
    const calls = stubAdmin({
      'GET /api/v1/admin/settings': () => jsonResponse(SETTINGS_VIEW),
      'PUT /api/v1/admin/settings': (() => {
        let attempt = 0
        return () => {
          attempt += 1
          if (attempt === 1) {
            return errorResponse(
              422,
              'LLM_VERIFICATION_FAILED',
              'Not saved — the new LLM configuration did not verify: provider rejected the API key.',
            )
          }
          return jsonResponse({ ...SETTINGS_VIEW, values: { ...SETTINGS_VIEW.values, llm_model: 'llama3.3' } })
        }
      })(),
    })
    render(<Harness />)
    await screen.findByText('AI / LLM provider')
    await userEvent.clear(screen.getByLabelText('Model'))
    await userEvent.type(screen.getByLabelText('Model'), 'llama3.3')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    const banner = await screen.findByRole('alert', { name: 'Save result' })
    expect(banner.textContent).toContain('did not verify')
    expect(banner.textContent).toContain('provider rejected the API key')
    // The old value is untouched (nothing was saved).
    expect((screen.getByLabelText('Model') as HTMLInputElement).value).toBe('llama3.3')
    // Explicit override: retry with force.
    await userEvent.click(within(banner).getByRole('button', { name: 'Save anyway' }))
    await waitFor(() =>
      expect(screen.getByRole('status', { name: 'Save result' }).textContent).toContain('Settings saved'),
    )
    const puts = calls.filter((c) => c.method === 'PUT')
    expect(JSON.parse(puts[0].body!).force).toBe(false)
    expect(JSON.parse(puts[1].body!).force).toBe(true)
  })

  it('queues explicit secret removal and sends clear_secrets on save', async () => {
    const calls = stubAdmin({
      'GET /api/v1/admin/settings': () => jsonResponse(SETTINGS_VIEW),
      'PUT /api/v1/admin/settings': () => jsonResponse(SETTINGS_VIEW),
    })
    render(<Harness />)
    await screen.findByText('AI / LLM provider')
    await userEvent.selectOptions(screen.getByLabelText('Provider'), 'openai')
    // A saved key exists (masked "set") → offer removal, never a blank overwrite.
    await userEvent.click(screen.getByRole('button', { name: 'Remove API key' }))
    expect(screen.getByText(/Key will be removed when you save changes/)).toBeTruthy()
    expect(screen.getByText('Unsaved changes')).toBeTruthy()
    // Undo cancels the removal (the provider switch itself still counts as
    // an unsaved change, so only the removal indicator is checked here).
    await userEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(screen.queryByText(/Key will be removed/)).toBeNull()
    expect(screen.getByRole('button', { name: 'Remove API key' })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Remove API key' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await screen.findByRole('status', { name: 'Save result' })
    const put = calls.find((c) => c.method === 'PUT')!
    const body = JSON.parse(put.body!)
    expect(body.clear_secrets).toEqual(['llm_api_key'])
    // Blank secret (unchanged) is not sent as a destructive empty string.
    expect(body.secrets.llm_api_key ?? '').toBe('')
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
    const calls = stubAdmin({
      'GET /api/v1/admin/settings': () => jsonResponse(SETTINGS_VIEW),
      'POST /api/v1/admin/test/llm': () =>
        jsonResponse({ success: true, latency_ms: 42, message: 'ollama / llama3.1: reachable.' }),
    })
    render(<Harness />)
    await screen.findByText('AI / LLM provider')
    await userEvent.click(screen.getByRole('button', { name: 'Test LLM connection' }))
    expect(await screen.findByText(/reachable\. \(42 ms\)/)).toBeTruthy()
    // The test posts the form's draft config, so unsaved edits are exercised.
    const test = calls.find((c) => c.path === '/api/v1/admin/test/llm')!
    expect(JSON.parse(test.body!).provider).toBe('ollama')
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
