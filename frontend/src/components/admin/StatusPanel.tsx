/** System status panel: truthful dependency states from GET /admin/status. */

import { useEffect, useState } from 'react'
import { fetchStatus, type DependencyStatus, type SystemStatus } from '../../lib/admin'

const DOT: Record<string, string> = {
  ok: 'bg-emerald-500',
  configured: 'bg-emerald-500',
  unavailable: 'bg-red-500',
  error: 'bg-red-500',
  not_configured: 'bg-ink-400',
}

function Row({
  name,
  status,
  detail,
}: {
  name: string
  status: DependencyStatus
  detail?: string
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-ink-100 py-2 text-sm last:border-b-0 dark:border-ink-800">
      <span className="font-medium">{name}</span>
      <span className="flex min-w-0 items-center gap-2 text-ink-500 dark:text-ink-400">
        <span aria-hidden className={`size-2 shrink-0 rounded-full ${DOT[status.status] ?? 'bg-ink-400'}`} />
        <span className="capitalize">{status.status.replace(/_/g, ' ')}</span>
        <span className="truncate">· {detail ?? status.detail}</span>
      </span>
    </div>
  )
}

export function StatusPanel() {
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchStatus().then(setStatus).catch((e: Error) => setError(e.message))
  }, [])

  if (error) {
    return (
      <p role="alert" className="rounded-xl bg-red-50 p-4 text-sm text-red-700 dark:bg-red-950/60 dark:text-red-300">
        {error}
      </p>
    )
  }
  if (!status) {
    return <p className="text-sm text-ink-500">Loading system status…</p>
  }

  return (
    <section
      aria-labelledby="sec-status"
      className="rounded-xl border border-ink-200 bg-white p-5 shadow-sm dark:border-ink-800 dark:bg-ink-900"
    >
      <h2 id="sec-status" className="font-serif text-lg font-bold">
        System status
      </h2>
      <p className="mt-1 text-sm text-ink-500 dark:text-ink-400">
        Live checks against configured dependencies. States are reported exactly as probed.
      </p>
      <div className="mt-3">
        <Row name="Backend" status={status.backend} detail={`v${String(status.backend.version ?? '?')}`} />
        {status.resources && (
          <div className="border-b border-ink-100 py-2 text-sm last:border-b-0 dark:border-ink-800">
            <div className="flex items-center justify-between gap-3">
              <span className="font-medium">Server resources</span>
              <span className="text-ink-500 dark:text-ink-400">
                {String(status.resources.cpu_cores)} CPU cores ·{' '}
                {status.resources.available_ram_mb != null
                  ? `${status.resources.available_ram_mb} MB free of ${status.resources.total_ram_mb ?? '?'} MB`
                  : 'RAM unknown'}
              </span>
            </div>
            {status.resources.warnings.length > 0 && (
              <ul className="mt-1.5 list-disc pl-5 text-xs text-amber-700 dark:text-amber-300">
                {status.resources.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            )}
          </div>
        )}
        <Row name="PostgreSQL" status={status.postgres} />
        <Row name="Redis" status={status.redis} />
        <Row name="Qdrant" status={status.qdrant} />
        <Row name="Worker" status={status.worker} />
        <Row
          name="LLM provider"
          status={status.llm}
          detail={[
            // Non-healthy classified state (degraded, invalid_configuration…)
            // is shown explicitly — "error" alone hides why.
            status.llm.state && status.llm.state !== 'healthy'
              ? status.llm.state.replace(/_/g, ' ')
              : null,
            `${String(status.llm.provider ?? '?')} / ${String(status.llm.model ?? '?')}`,
          ]
            .filter(Boolean)
            .join(' · ')}
        />
        <Row name="Speech (STT)" status={status.stt} detail={`${status.stt.provider ?? ''} ${status.stt.model ?? ''}`} />
        <Row name="Speech (TTS)" status={status.tts} detail={`${status.tts.provider ?? ''} ${status.tts.model ?? ''}`} />
        <Row name="Corpus" status={status.corpus} detail={status.corpus.act ?? status.corpus.detail} />
      </div>
    </section>
  )
}
