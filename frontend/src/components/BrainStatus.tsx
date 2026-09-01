/**
 * Header system-status indicator for the active LLM provider.
 * Subtle by design. Truthful by construction: the label derives only from
 * the backend's classified provider probe (`/api/v1/health/llm`) — never
 * from local state like "a settings object exists" or "a provider is
 * selected". "Brain active" means the active provider is configured,
 * authenticated AND its model is available.
 */

import { useEffect, useState } from 'react'
import { fetchModelHealth, type ModelHealth } from '../lib/api'

const POLL_MS = 30_000

const LABELS: Record<ModelHealth, string> = {
  active: 'Brain active',
  degraded: 'Brain degraded',
  unavailable: 'Brain unavailable',
  misconfigured: 'Brain misconfigured',
  not_configured: 'Brain not configured',
  unknown: 'Brain status unknown',
}

const DOTS: Record<ModelHealth, string> = {
  active: 'bg-brand-500',
  degraded: 'bg-amber-500',
  unavailable: 'bg-red-500',
  misconfigured: 'bg-red-500',
  not_configured: 'bg-ink-400',
  unknown: 'bg-ink-400',
}

const TITLES: Record<ModelHealth, string> = {
  active: 'The active LLM provider answered the backend health probe and offers the configured model.',
  degraded: 'The provider is reachable, but the configured model is not available — chat may fail.',
  unavailable: 'The backend reports the LLM provider is not reachable.',
  misconfigured: 'The provider rejected the configuration (e.g. an invalid API key or base URL).',
  not_configured: 'No usable LLM provider is configured.',
  unknown: 'The LLM provider status could not be determined.',
}

export function BrainStatus() {
  const [state, setState] = useState<ModelHealth>('unknown')

  useEffect(() => {
    let cancelled = false
    const update = async () => {
      const next = await fetchModelHealth()
      if (!cancelled) setState(next)
    }
    void update()
    const timer = window.setInterval(() => void update(), POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  const label = LABELS[state]

  return (
    <p
      role="status"
      aria-label={`System status: ${label}`}
      title={TITLES[state]}
      className="flex items-center gap-1.5 rounded-full border border-ink-200 bg-ink-50 px-2.5 py-1 text-[11px] font-medium text-ink-600 dark:border-ink-800 dark:bg-ink-900 dark:text-ink-300"
    >
      <span className="relative flex size-2" aria-hidden="true">
        {state === 'active' && (
          <span className="absolute inline-flex size-2 animate-ping rounded-full bg-brand-400 opacity-60 motion-reduce:animate-none" />
        )}
        <span className={`relative inline-flex size-2 rounded-full ${DOTS[state]}`} />
      </span>
      <span className="hidden md:inline">{label}</span>
    </p>
  )
}
