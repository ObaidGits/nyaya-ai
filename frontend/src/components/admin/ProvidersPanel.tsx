/**
 * Provider pools panel (failover): configure multiple LLM/STT/TTS
 * providers, enable/disable, set the default, order by priority, choose a
 * failover strategy, test credentials before saving, and see live health.
 * An empty pool means "use the environment's single provider" — unchanged
 * pre-pool behavior.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  fetchProviders,
  testPoolEntry,
  updateProviders,
  type PoolEntry,
  type PoolName,
  type ProvidersView,
  type TestResult,
} from '../../lib/admin'
import { ApiError } from '../../lib/api'

type DraftEntry = Omit<PoolEntry, 'api_key_set' | 'health'> & {
  api_key_set: boolean
}

interface DraftPool {
  entries: DraftEntry[]
  default_entry_id: string | null
  strategy: 'priority' | 'round_robin'
}

type Drafts = Record<PoolName, DraftPool>

const POOLS: { name: PoolName; title: string; help: string }[] = [
  {
    name: 'llm',
    title: 'LLM (answer generation)',
    help: 'Providers tried in order when answering questions. The default is used first; on failure the next enabled entry takes over automatically.',
  },
  {
    name: 'stt',
    title: 'Speech-to-text',
    help: 'Transcription providers with the same failover behavior.',
  },
  {
    name: 'tts',
    title: 'Text-to-speech',
    help: 'Synthesis providers with the same failover behavior.',
  },
]

const inputClass =
  'w-full rounded-lg border border-ink-300 bg-white px-2.5 py-1.5 text-sm outline-none transition-colors focus:border-brand-600 focus:ring-2 focus:ring-brand-600/30 dark:border-ink-700 dark:bg-ink-950'

interface SaveResult {
  kind: 'success' | 'error' | 'verify'
  message: string
}

function healthBadge(entry: PoolEntry): { label: string; className: string; title: string } {
  const health = entry.health
  if (!entry.enabled) {
    return { label: 'disabled', className: 'bg-ink-100 text-ink-500 dark:bg-ink-800 dark:text-ink-400', title: 'Never selected' }
  }
  if (health.state === 'healthy') {
    return { label: 'healthy', className: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300', title: 'Served a request successfully' }
  }
  if (health.state === 'cooling') {
    return {
      label: 'cooling down',
      className: 'bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300',
      title: health.last_error ? `Last error (${health.last_error_class ?? 'unknown'}): ${health.last_error}` : 'In cooldown after failures',
    }
  }
  return { label: 'untested', className: 'bg-ink-100 text-ink-500 dark:bg-ink-800 dark:text-ink-400', title: 'Not used since startup' }
}

function draftFromView(view: ProvidersView): Drafts {
  const toDraft = (entry: PoolEntry): DraftEntry => ({
    id: entry.id,
    provider: entry.provider,
    label: entry.label,
    model: entry.model,
    base_url: entry.base_url,
    enabled: entry.enabled,
    priority: entry.priority,
    api_key_set: entry.api_key_set,
  })
  return {
    llm: {
      entries: view.pools.llm.entries.map(toDraft),
      default_entry_id: view.pools.llm.default_entry_id,
      strategy: view.pools.llm.strategy,
    },
    stt: {
      entries: view.pools.stt.entries.map(toDraft),
      default_entry_id: view.pools.stt.default_entry_id,
      strategy: view.pools.stt.strategy,
    },
    tts: {
      entries: view.pools.tts.entries.map(toDraft),
      default_entry_id: view.pools.tts.default_entry_id,
      strategy: view.pools.tts.strategy,
    },
  }
}

export function ProvidersPanel() {
  const [view, setView] = useState<ProvidersView | null>(null)
  const [drafts, setDrafts] = useState<Drafts | null>(null)
  /** New/rotated keys, keyed "pool:<entry_id>" (blank = keep stored key). */
  const [newKeys, setNewKeys] = useState<Record<string, string>>({})
  const [clearedKeys, setClearedKeys] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [saveResult, setSaveResult] = useState<SaveResult | null>(null)
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({})

  const load = useCallback(async () => {
    try {
      const next = await fetchProviders()
      setView(next)
      setDrafts(draftFromView(next))
      setNewKeys({})
      setClearedKeys([])
    } catch {
      setView(null)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const dirty = useMemo(() => {
    if (!view || !drafts) return false
    return (
      JSON.stringify(drafts) !== JSON.stringify(draftFromView(view)) ||
      Object.values(newKeys).some((value) => value !== '') ||
      clearedKeys.length > 0
    )
  }, [view, drafts, newKeys, clearedKeys])

  if (!view || !drafts) {
    return (
      <section className="rounded-xl border border-ink-200 bg-white p-5 dark:border-ink-800 dark:bg-ink-900">
        <h2 className="font-serif text-base font-bold">Provider pools</h2>
        <p className="mt-1 text-sm text-ink-500">Loading providers…</p>
      </section>
    )
  }

  const providerOptions = (pool: PoolName): string[] =>
    pool === 'llm'
      ? view.registered_llm_providers
      : pool === 'stt'
        ? view.speech_stt_providers
        : view.speech_tts_providers

  const patchPool = (pool: PoolName, patch: Partial<DraftPool>) =>
    setDrafts((prev) => prev && { ...prev, [pool]: { ...prev[pool], ...patch } })

  const patchEntry = (pool: PoolName, entryId: string, patch: Partial<DraftEntry>) =>
    setDrafts((prev) =>
      prev && {
        ...prev,
        [pool]: {
          ...prev[pool],
          entries: prev[pool].entries.map((entry) =>
            entry.id === entryId ? { ...entry, ...patch } : entry,
          ),
        },
      },
    )

  const addEntry = (pool: PoolName) => {
    const options = providerOptions(pool)
    const base = options[0] ?? ''
    let suffix = 1
    const ids = new Set(drafts[pool].entries.map((entry) => entry.id))
    while (ids.has(`entry-${suffix}`)) suffix += 1
    patchPool(pool, {
      entries: [
        ...drafts[pool].entries,
        {
          id: `entry-${suffix}`,
          provider: base,
          label: '',
          model: '',
          base_url: '',
          enabled: true,
          priority: (drafts[pool].entries.length + 1) * 10,
          api_key_set: false,
        },
      ],
      default_entry_id: drafts[pool].default_entry_id ?? `entry-${suffix}`,
    })
  }

  const removeEntry = (pool: PoolName, entryId: string) => {
    const entries = drafts[pool].entries.filter((entry) => entry.id !== entryId)
    patchPool(pool, {
      entries,
      default_entry_id:
        drafts[pool].default_entry_id === entryId
          ? (entries[0]?.id ?? null)
          : drafts[pool].default_entry_id,
    })
    const key = `pool:${pool}:${entryId}`
    setNewKeys((prev) => {
      const next = { ...prev }
      delete next[key]
      return next
    })
    if (view.pools[pool].entries.some((entry) => entry.id === entryId && entry.api_key_set)) {
      setClearedKeys((prev) => [...prev, key])
    }
  }

  const save = async (force = false) => {
    setSaving(true)
    try {
      // newKeys is keyed by the backend's secret-key format
      // ("pool:<name>:<entry_id>") — pass through as-is, blanks = keep stored.
      const secrets = Object.fromEntries(
        Object.entries(newKeys).filter(([, value]) => value !== ''),
      )
      const next = await updateProviders(
        {
          llm: drafts.llm,
          stt: drafts.stt,
          tts: drafts.tts,
        },
        secrets,
        { clearSecrets: clearedKeys, force },
      )
      setView(next)
      setDrafts(draftFromView(next))
      setNewKeys({})
      setClearedKeys([])
      setSaveResult({ kind: 'success', message: 'Provider pools saved and active.' })
    } catch (error) {
      if (error instanceof ApiError && error.code === 'PROVIDER_POOL_VERIFY_FAILED') {
        setSaveResult({ kind: 'verify', message: error.message })
      } else {
        const message = error instanceof Error ? error.message : 'Save failed.'
        setSaveResult({ kind: 'error', message })
      }
    } finally {
      setSaving(false)
    }
  }

  const testEntry = async (pool: PoolName, entry: DraftEntry) => {
    const key = `${pool}:${entry.id}`
    try {
      const result = await testPoolEntry(pool, entry, newKeys[`pool:${key}`] ?? '')
      setTestResults((prev) => ({ ...prev, [key]: result }))
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Test failed.'
      setTestResults((prev) => ({ ...prev, [key]: { success: false, latency_ms: null, message } }))
    }
  }

  return (
    <section className="rounded-xl border border-ink-200 bg-white p-5 dark:border-ink-800 dark:bg-ink-900">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="font-serif text-base font-bold">Provider pools &amp; failover</h2>
          <p className="mt-1 text-sm text-ink-500 dark:text-ink-400">
            Multiple providers per capability with automatic failover. Leave a pool empty to use
            the environment default (
            {view.env_fallback.llm_provider} / {view.env_fallback.llm_model} for LLM).
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void load()}
            className="rounded-lg border border-ink-300 px-3 py-1.5 text-sm text-ink-700 transition-colors hover:bg-ink-100 disabled:opacity-50 dark:border-ink-700 dark:text-ink-300 dark:hover:bg-ink-800"
          >
            Refresh health
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={!dirty || saving}
            className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-brand-700 disabled:opacity-50 dark:bg-brand-500 dark:text-ink-950"
          >
            {saving ? 'Saving…' : 'Save pools'}
          </button>
        </div>
      </div>

      {saveResult && (
        <div
          role={saveResult.kind === 'success' ? 'status' : 'alert'}
          aria-label="Pool save result"
          className={`mt-4 flex flex-wrap items-start justify-between gap-3 rounded-lg border p-3 text-sm ${
            saveResult.kind === 'success'
              ? 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300'
              : saveResult.kind === 'verify'
                ? 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-300'
                : 'border-red-300 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950/60 dark:text-red-300'
          }`}
        >
          <p className="min-w-0 flex-1 whitespace-pre-line">{saveResult.message}</p>
          <div className="flex shrink-0 gap-2">
            {saveResult.kind === 'verify' && (
              <button
                type="button"
                onClick={() => void save(true)}
                disabled={saving}
                className="rounded-lg border border-amber-600 px-3 py-1 text-xs font-medium text-amber-800 transition-colors hover:bg-amber-100 disabled:opacity-50 dark:border-amber-500 dark:text-amber-300"
              >
                Save anyway
              </button>
            )}
            <button
              type="button"
              onClick={() => setSaveResult(null)}
              aria-label="Dismiss message"
              className="rounded-lg px-2 py-1 text-xs underline-offset-2 transition-colors hover:underline"
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {POOLS.map(({ name: pool, title, help }) => {
        const draft = drafts[pool]
        const savedPool = view.pools[pool]
        const options = providerOptions(pool)
        return (
          <div key={pool} className="mt-5 rounded-lg border border-ink-200 p-4 dark:border-ink-800">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold">{title}</h3>
              <div className="flex items-center gap-2 text-xs">
                <span
                  className={`rounded-full px-2 py-0.5 font-medium ${
                    savedPool.mode === 'pool'
                      ? 'bg-brand-100 text-brand-800 dark:bg-brand-950/60 dark:text-brand-300'
                      : 'bg-ink-100 text-ink-500 dark:bg-ink-800 dark:text-ink-400'
                  }`}
                >
                  {savedPool.mode === 'pool' ? 'pool with failover' : 'environment default'}
                </span>
                <label className="flex items-center gap-1">
                  <span className="text-ink-500 dark:text-ink-400">Strategy</span>
                  <select
                    aria-label={`${title} failover strategy`}
                    value={draft.strategy}
                    onChange={(e) =>
                      patchPool(pool, { strategy: e.target.value as DraftPool['strategy'] })
                    }
                    className="rounded-lg border border-ink-300 bg-white px-2 py-1 text-xs dark:border-ink-700 dark:bg-ink-950"
                  >
                    <option value="priority">priority (default first)</option>
                    <option value="round_robin">round robin</option>
                  </select>
                </label>
              </div>
            </div>
            <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">{help}</p>

            {draft.entries.length === 0 ? (
              <p className="mt-3 text-sm text-ink-500 dark:text-ink-400">
                No pool configured — the environment provider is used with no failover.
              </p>
            ) : (
              <ul className="mt-3 flex flex-col gap-3">
                {draft.entries.map((entry) => {
                  const saved = savedPool.entries.find((e) => e.id === entry.id)
                  const badge = saved ? healthBadge(saved) : null
                  const testKey = `${pool}:${entry.id}`
                  const test = testResults[testKey]
                  const keyField = `pool:${pool}:${entry.id}`
                  return (
                    <li
                      key={entry.id}
                      className="rounded-lg border border-ink-200 p-3 dark:border-ink-800"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <label className="flex items-center gap-1.5 text-xs">
                          <input
                            type="radio"
                            name={`${pool}-default`}
                            aria-label={`Default ${title} provider`}
                            checked={draft.default_entry_id === entry.id}
                            onChange={() => patchPool(pool, { default_entry_id: entry.id })}
                            className="size-4 accent-brand-600"
                          />
                          default
                        </label>
                        <label className="flex items-center gap-1.5 text-xs">
                          <input
                            type="checkbox"
                            aria-label={`Enable ${entry.id}`}
                            checked={entry.enabled}
                            onChange={(e) => patchEntry(pool, entry.id, { enabled: e.target.checked })}
                            className="size-4 accent-brand-600"
                          />
                          enabled
                        </label>
                        <label className="flex items-center gap-1 text-xs">
                          priority
                          <input
                            type="number"
                            min={0}
                            max={10000}
                            value={entry.priority}
                            onChange={(e) =>
                              patchEntry(pool, entry.id, { priority: Number(e.target.value) })
                            }
                            aria-label={`Priority for ${entry.id}`}
                            className="w-16 rounded border border-ink-300 bg-white px-1.5 py-0.5 text-xs dark:border-ink-700 dark:bg-ink-950"
                          />
                        </label>
                        {badge && (
                          <span
                            title={badge.title}
                            className={`rounded-full px-2 py-0.5 text-xs font-medium ${badge.className}`}
                          >
                            {badge.label}
                          </span>
                        )}
                        <span className="font-mono text-xs text-ink-400">{entry.id}</span>
                        <div className="ml-auto flex gap-2">
                          <button
                            type="button"
                            onClick={() => void testEntry(pool, entry)}
                            className="rounded-lg border border-ink-300 px-2.5 py-1 text-xs text-ink-700 transition-colors hover:bg-ink-100 dark:border-ink-700 dark:text-ink-300 dark:hover:bg-ink-800"
                          >
                            Test
                          </button>
                          <button
                            type="button"
                            onClick={() => removeEntry(pool, entry.id)}
                            aria-label={`Remove ${entry.id}`}
                            className="rounded-lg border border-red-300 px-2.5 py-1 text-xs text-red-700 transition-colors hover:bg-red-50 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950/40"
                          >
                            Remove
                          </button>
                        </div>
                      </div>

                      <div className="mt-2 grid gap-2 sm:grid-cols-2">
                        <label className="text-xs">
                          Provider
                          <select
                            value={entry.provider}
                            onChange={(e) => patchEntry(pool, entry.id, { provider: e.target.value })}
                            className={inputClass}
                          >
                            {options.map((option) => (
                              <option key={option} value={option}>
                                {option}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="text-xs">
                          Model {pool === 'llm' && '(blank = environment default)'}
                          <input
                            type="text"
                            value={entry.model}
                            placeholder="e.g. llama3.1"
                            onChange={(e) => patchEntry(pool, entry.id, { model: e.target.value })}
                            className={inputClass}
                          />
                        </label>
                        <label className="text-xs">
                          Base URL (blank = provider default)
                          <input
                            type="text"
                            value={entry.base_url}
                            placeholder="https://…"
                            onChange={(e) => patchEntry(pool, entry.id, { base_url: e.target.value })}
                            className={inputClass}
                          />
                        </label>
                        <label className="text-xs">
                          API key{' '}
                          {entry.api_key_set && (
                            <span className="text-emerald-700 dark:text-emerald-400">(saved)</span>
                          )}
                          <input
                            type="password"
                            value={newKeys[keyField] ?? ''}
                            placeholder={entry.api_key_set ? 'leave blank to keep' : 'sk-…'}
                            autoComplete="new-password"
                            onChange={(e) =>
                              setNewKeys((prev) => ({ ...prev, [keyField]: e.target.value }))
                            }
                            className={inputClass}
                          />
                        </label>
                      </div>

                      {test && (
                        <p
                          role="status"
                          className={`mt-2 text-xs ${
                            test.success
                              ? 'text-emerald-700 dark:text-emerald-400'
                              : 'text-red-600 dark:text-red-400'
                          }`}
                        >
                          {test.success ? '✓ ' : '✗ '}
                          {test.message}
                          {test.latency_ms !== null && test.latency_ms !== undefined
                            ? ` (${test.latency_ms} ms)`
                            : ''}
                        </p>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}

            <button
              type="button"
              onClick={() => addEntry(pool)}
              className="mt-3 rounded-lg border border-dashed border-ink-300 px-3 py-1.5 text-sm text-ink-600 transition-colors hover:bg-ink-100 dark:border-ink-700 dark:text-ink-300 dark:hover:bg-ink-800"
            >
              + Add provider
            </button>
          </div>
        )
      })}
    </section>
  )
}
