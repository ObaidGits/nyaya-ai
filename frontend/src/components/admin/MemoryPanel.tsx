/** Memory panel: documents the real memory architecture; only safe knobs. */

import { useEffect, useState } from 'react'
import { fetchMemory, updateMemory } from '../../lib/admin'

export function MemoryPanel() {
  const [maxTurns, setMaxTurns] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    fetchMemory().then((info) => setMaxTurns(info.history_max_turns))
  }, [])

  if (maxTurns === null) return null

  const save = async () => {
    setSaving(true)
    try {
      await updateMemory(maxTurns)
      setSaved(true)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section
      aria-labelledby="sec-memory"
      className="rounded-xl border border-ink-200 bg-white p-5 shadow-sm dark:border-ink-800 dark:bg-ink-900"
    >
      <h2 id="sec-memory" className="font-serif text-lg font-bold">
        Conversation memory
      </h2>
      <p className="mt-1 text-sm text-ink-500 dark:text-ink-400">
        Memory is client-side conversation history sent with each request, capped server-side.
        History is untrusted context — never a source of legal authority. Clearing conversations
        is a browser action in the main app.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-sm font-medium" htmlFor="memory-turns">
            History window (turns)
          </label>
          <input
            id="memory-turns"
            type="number"
            min={1}
            max={50}
            value={maxTurns}
            onChange={(e) => {
              setMaxTurns(Number(e.target.value))
              setSaved(false)
            }}
            className="mt-1 w-32 rounded-lg border border-ink-300 bg-white px-3 py-2 text-sm outline-none focus:border-brand-600 dark:border-ink-700 dark:bg-ink-950"
          />
        </div>
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50 dark:bg-brand-500 dark:text-ink-950"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        {saved && (
          <p role="status" className="text-sm text-emerald-700 dark:text-emerald-400">
            Saved.
          </p>
        )}
      </div>
    </section>
  )
}
