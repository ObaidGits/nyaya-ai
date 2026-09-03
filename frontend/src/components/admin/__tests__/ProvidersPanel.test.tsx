/**
 * ProvidersPanel tests: renders pool state truthfully, drafts edits, saves
 * the full pool config, and offers "Save anyway" after the backend's
 * per-entry verification gate rejects the candidate.
 */

import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ProvidersPanel } from '../ProvidersPanel'

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

const PROVIDERS_VIEW = {
  pools: {
    llm: {
      entries: [
        {
          id: 'groq-main',
          provider: 'groq',
          label: '',
          model: 'openai/gpt-oss-120b',
          base_url: '',
          enabled: true,
          priority: 10,
          api_key_set: true,
          health: { state: 'healthy' },
        },
        {
          id: 'ollama-backup',
          provider: 'ollama',
          label: '',
          model: 'llama3.1',
          base_url: '',
          enabled: true,
          priority: 20,
          api_key_set: false,
          health: { state: 'cooling', last_error: 'unreachable', last_error_class: 'transient' },
        },
      ],
      default_entry_id: 'groq-main',
      strategy: 'priority',
      mode: 'pool',
    },
    stt: { entries: [], default_entry_id: null, strategy: 'priority', mode: 'environment' },
    tts: { entries: [], default_entry_id: null, strategy: 'priority', mode: 'environment' },
  },
  registered_llm_providers: ['gemini', 'groq', 'ollama'],
  speech_stt_providers: ['browser', 'faster-whisper'],
  speech_tts_providers: ['browser', 'piper'],
  env_fallback: {
    llm_provider: 'ollama',
    llm_model: 'llama3.1',
    speech_stt_provider: 'faster-whisper',
    speech_tts_provider: 'piper',
  },
}

function stubAdmin(calls: Record<string, () => Response>) {
  const callsMade: { method: string; path: string; body?: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      const path = new URL(url, 'http://x').pathname
      const method = (init?.method ?? 'GET').toUpperCase()
      callsMade.push({ method, path, body: typeof init?.body === 'string' ? init.body : undefined })
      const handler = calls[`${method} ${path}`]
      if (!handler) return Promise.resolve(errorResponse(404, 'NOT_FOUND', 'no stub'))
      return Promise.resolve(handler())
    }),
  )
  return callsMade
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('ProvidersPanel', () => {
  it('renders pool state truthfully (mode, health, default)', async () => {
    stubAdmin({ 'GET /api/v1/admin/providers': () => jsonResponse(PROVIDERS_VIEW) })
    render(<ProvidersPanel />)
    expect(await screen.findByText('Provider pools & failover')).toBeTruthy()
    // Mode badges: LLM pool active, STT still environment.
    expect(screen.getByText('pool with failover')).toBeTruthy()
    expect(screen.getAllByText('environment default').length).toBe(2)
    // Health badges from the runtime board.
    expect(screen.getByText('healthy')).toBeTruthy()
    expect(screen.getByText('cooling down')).toBeTruthy()
    // Default marker reflects the persisted default entry.
    expect(screen.getByText('groq-main')).toBeTruthy()
  })

  it('drafts edits and saves the full pool config', async () => {
    const calls = stubAdmin({
      'GET /api/v1/admin/providers': () => jsonResponse(PROVIDERS_VIEW),
      'PUT /api/v1/admin/providers': () => jsonResponse(PROVIDERS_VIEW),
    })
    render(<ProvidersPanel />)
    await screen.findByText('groq-main')

    // Change the model of the default entry.
    const model = screen.getByDisplayValue('openai/gpt-oss-120b') as HTMLInputElement
    await userEvent.clear(model)
    await userEvent.type(model, 'llama-3.3-70b')

    await userEvent.click(screen.getByRole('button', { name: 'Save pools' }))
    await waitFor(() => {
      const put = calls.find((call) => call.method === 'PUT' && call.path === '/api/v1/admin/providers')
      expect(put).toBeTruthy()
      const body = JSON.parse(put!.body!)
      expect(body.pools.llm.entries[0].model).toBe('llama-3.3-70b')
      expect(body.pools.llm.default_entry_id).toBe('groq-main')
      expect(body.force).toBe(false)
    })
  })

  it('offers Save anyway when entry verification fails', async () => {
    stubAdmin({
      'GET /api/v1/admin/providers': () => jsonResponse(PROVIDERS_VIEW),
      'PUT /api/v1/admin/providers': () =>
        errorResponse(
          422,
          'PROVIDER_POOL_VERIFY_FAILED',
          'Not saved — these pool entries did not verify:\nllm:groq-main — bad key',
        ),
    })
    render(<ProvidersPanel />)
    await screen.findByText('groq-main')
    await userEvent.type(screen.getByDisplayValue('llama3.1'), '-edit')
    await userEvent.click(screen.getByRole('button', { name: 'Save pools' }))
    expect(await screen.findByText(/did not verify/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Save anyway' })).toBeTruthy()
  })

  it('saves with force after "Save anyway"', async () => {
    let putCount = 0
    const calls = stubAdmin({
      'GET /api/v1/admin/providers': () => jsonResponse(PROVIDERS_VIEW),
      'PUT /api/v1/admin/providers': () => {
        putCount += 1
        if (putCount === 1) {
          return errorResponse(422, 'PROVIDER_POOL_VERIFY_FAILED', 'Not saved — did not verify.')
        }
        return jsonResponse(PROVIDERS_VIEW)
      },
    })
    render(<ProvidersPanel />)
    await screen.findByText('groq-main')
    await userEvent.type(screen.getByDisplayValue('llama3.1'), '-edit')
    await userEvent.click(screen.getByRole('button', { name: 'Save pools' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Save anyway' }))
    await waitFor(() => {
      const puts = calls.filter((call) => call.method === 'PUT')
      expect(puts).toHaveLength(2)
      expect(JSON.parse(puts[1].body!).force).toBe(true)
    })
  })

  it('adds an entry and marks it default when the pool was empty', async () => {
    const empty = {
      ...PROVIDERS_VIEW,
      pools: {
        ...PROVIDERS_VIEW.pools,
        llm: { entries: [], default_entry_id: null, strategy: 'priority', mode: 'environment' },
      },
    }
    const calls = stubAdmin({
      'GET /api/v1/admin/providers': () => jsonResponse(empty),
      'PUT /api/v1/admin/providers': () => jsonResponse(empty),
    })
    render(<ProvidersPanel />)
    expect(
      (await screen.findAllByText('No pool configured — the environment provider is used with no failover.')).length,
    ).toBe(3)
    await userEvent.click(screen.getAllByRole('button', { name: '+ Add provider' })[0])
    await userEvent.click(screen.getByRole('button', { name: 'Save pools' }))
    await waitFor(() => {
      const put = calls.find((call) => call.method === 'PUT')
      const body = JSON.parse(put!.body!)
      expect(body.pools.llm.entries).toHaveLength(1)
      expect(body.pools.llm.entries[0].id).toBe('entry-1')
      // First entry in an empty pool becomes the default automatically.
      expect(body.pools.llm.default_entry_id).toBe('entry-1')
    })
  })

  it('shows per-entry test results', async () => {
    stubAdmin({
      'GET /api/v1/admin/providers': () => jsonResponse(PROVIDERS_VIEW),
      'POST /api/v1/admin/providers/test': () =>
        jsonResponse({ success: true, latency_ms: 412, message: 'groq/openai/gpt-oss-120b: chat round-trip verified.' }),
    })
    render(<ProvidersPanel />)
    await screen.findByText('groq-main')
    await userEvent.click(screen.getAllByRole('button', { name: 'Test' })[0])
    expect(await screen.findByText(/chat round-trip verified/)).toBeTruthy()
  })
})
