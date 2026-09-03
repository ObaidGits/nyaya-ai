/**
 * Admin console shell (D-080): session gate, settings draft tracking,
 * save/reset with unsaved-changes indicator, corpus / status / memory panels.
 * Reached only via the #settings hash — never linked from the main nav.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  adminLogout,
  fetchSession,
  fetchSettings,
  updateSettings,
  type AdminSettingsView,
} from '../../lib/admin'
import { ADMIN_SECTIONS } from '../../lib/adminSchema'
import { ApiError } from '../../lib/api'
import { AdminLogin } from './AdminLogin'
import { SettingsSectionCard } from './SettingsSectionCard'
import { ProvidersPanel } from './ProvidersPanel'
import { CorpusPanel } from './CorpusPanel'
import { StatusPanel } from './StatusPanel'
import { MemoryPanel } from './MemoryPanel'

type Values = Record<string, string | number | boolean>
type Secrets = Record<string, string>

/** Save outcome shown as a persistent banner (§ save UX). "verify" offers
 * "Save anyway" after the backend's test-before-activate gate rejected the
 * candidate — the previous provider stays active until then. */
interface SaveResult {
  kind: 'success' | 'error' | 'verify'
  message: string
}

export function AdminPanel({ onExit }: { onExit: () => void }) {
  const [authed, setAuthed] = useState<boolean | null>(null)
  const [enabled, setEnabled] = useState(true)
  const [view, setView] = useState<AdminSettingsView | null>(null)
  const [draftValues, setDraftValues] = useState<Values>({})
  const [draftSecrets, setDraftSecrets] = useState<Secrets>({})
  const [clearedSecrets, setClearedSecrets] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [saveResult, setSaveResult] = useState<SaveResult | null>(null)
  // The save banner renders above the (long) settings list; when the admin
  // saved from a scrolled position, pull it into view so the outcome is seen.
  const saveResultRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    // Optional call: jsdom (tests) does not implement scrollIntoView.
    if (saveResult) saveResultRef.current?.scrollIntoView?.({ block: 'nearest' })
  }, [saveResult])

  const loadSettings = useCallback(async () => {
    const next = await fetchSettings()
    setView(next)
    setDraftValues({ ...next.values })
    setDraftSecrets({})
    setClearedSecrets([])
  }, [])

  useEffect(() => {
    fetchSession().then((session) => {
      setEnabled(session.enabled)
      setAuthed(session.authenticated)
      if (session.authenticated) void loadSettings()
    })
  }, [loadSettings])

  const dirty =
    view !== null &&
    (ADMIN_SECTIONS.some((section) =>
      section.fields.some((field) => {
        const current = String(draftValues[field.key] ?? '')
        return String(view.values[field.key] ?? '') !== current
      }),
    ) ||
      Object.values(draftSecrets).some((value) => value !== '') ||
      clearedSecrets.length > 0)

  // Guard against losing unsaved changes on tab close / reload. Unconditional
  // (must run even while the early-return screens below are shown).
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  if (authed === null) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <p className="text-sm text-ink-500">Loading admin console…</p>
      </div>
    )
  }

  if (!enabled) {
    return (
      <div className="mx-auto flex min-h-dvh max-w-md flex-col justify-center p-6 text-center">
        <h1 className="font-serif text-xl font-bold">Admin console disabled</h1>
        <p className="mt-2 text-sm text-ink-500">
          Set ADMIN_USERNAME and ADMIN_PASSWORD on the server to enable it.
        </p>
        <button type="button" onClick={onExit} className="mt-4 text-sm text-brand-700 underline dark:text-brand-300">
          Back to Nyaya
        </button>
      </div>
    )
  }

  if (!authed) return <AdminLogin onAuthenticated={() => { setAuthed(true); void loadSettings() }} />
  if (!view) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <p className="text-sm text-ink-500">Loading settings…</p>
      </div>
    )
  }

  const onValueChange = (key: string, value: string | number | boolean) =>
    setDraftValues((prev) => {
      const next = { ...prev, [key]: value }
      // Switching provider resets a custom base URL: providers with a fixed
      // API endpoint ignore an empty URL, and a stale one from the previous
      // provider would silently override the default.
      if (key === 'llm_provider') next.llm_base_url = ''
      return next
    })

  const onSecretChange = (key: string, value: string) => {
    setDraftSecrets((prev) => ({ ...prev, [key]: value }))
    // Typing a new value cancels a pending removal of that key.
    if (value !== '') setClearedSecrets((prev) => prev.filter((k) => k !== key))
  }

  // Toggle explicit secret removal (empty input means "keep", not "clear").
  const onSecretClear = (key: string) => {
    setClearedSecrets((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key],
    )
    setDraftSecrets((prev) => {
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  const save = async (force = false) => {
    setSaving(true)
    try {
      const next = await updateSettings(draftValues, draftSecrets, {
        clearSecrets: clearedSecrets,
        force,
      })
      setView(next)
      setDraftValues({ ...next.values })
      setDraftSecrets({})
      setClearedSecrets([])
      const provider = String(next.values.llm_provider ?? '')
      const model = String(next.values.llm_model ?? '')
      setSaveResult({
        kind: 'success',
        message: `Settings saved — ${provider} / ${model} is now the active provider.`,
      })
    } catch (error) {
      if (error instanceof ApiError && error.code === 'LLM_VERIFICATION_FAILED') {
        // The candidate failed verification; nothing was saved. Offer the
        // explicit escape hatch rather than a generic error.
        setSaveResult({ kind: 'verify', message: error.message })
      } else {
        const message = error instanceof Error ? error.message : 'Save failed.'
        setSaveResult({ kind: 'error', message })
      }
    } finally {
      setSaving(false)
    }
  }

  const reset = () => {
    setDraftValues({ ...view.values })
    setDraftSecrets({})
    setClearedSecrets([])
  }

  const secretSet = Object.fromEntries(
    Object.entries(view.secrets).map(([key, value]) => [key, value === 'set']),
  )
  const secretSources = view.secret_sources ?? {}

  return (
    <div className="min-h-dvh bg-ink-50 dark:bg-ink-950">
      <header className="sticky top-0 z-10 flex h-14 items-center justify-between gap-3 border-b border-ink-200 bg-white/90 px-4 backdrop-blur dark:border-ink-800 dark:bg-ink-900/90">
        <div className="flex items-center gap-3">
          <h1 className="font-serif text-lg font-bold">Nyaya admin</h1>
          {dirty && (
            <span
              role="status"
              className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950/60 dark:text-amber-300"
            >
              Unsaved changes
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={reset}
            disabled={!dirty || saving}
            className="rounded-lg border border-ink-300 px-3 py-1.5 text-sm text-ink-700 transition-colors hover:bg-ink-100 disabled:opacity-50 dark:border-ink-700 dark:text-ink-300 dark:hover:bg-ink-800"
          >
            Reset
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={!dirty || saving}
            className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-brand-700 disabled:opacity-50 dark:bg-brand-500 dark:text-ink-950"
          >
            {saving ? 'Saving…' : 'Save changes'}
          </button>
          <button
            type="button"
            onClick={async () => {
              await adminLogout()
              setAuthed(false)
              setView(null)
            }}
            className="rounded-lg px-3 py-1.5 text-sm text-ink-600 transition-colors hover:bg-ink-100 dark:text-ink-300 dark:hover:bg-ink-800"
          >
            Sign out
          </button>
          <button
            type="button"
            onClick={onExit}
            className="rounded-lg px-3 py-1.5 text-sm text-ink-600 transition-colors hover:bg-ink-100 dark:text-ink-300 dark:hover:bg-ink-800"
          >
            Back to app
          </button>
        </div>
      </header>

      <main className="mx-auto flex max-w-3xl flex-col gap-5 p-4 sm:p-6">
        {saveResult && (
          <div
            ref={saveResultRef}
            role={saveResult.kind === 'success' ? 'status' : 'alert'}
            aria-label="Save result"
            className={`flex flex-wrap items-start justify-between gap-3 rounded-xl border p-4 text-sm ${
              saveResult.kind === 'success'
                ? 'border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300'
                : saveResult.kind === 'verify'
                  ? 'border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-300'
                  : 'border-red-300 bg-red-50 text-red-700 dark:border-red-800 dark:bg-red-950/60 dark:text-red-300'
            }`}
          >
            <p className="min-w-0 flex-1">{saveResult.message}</p>
            <div className="flex shrink-0 gap-2">
              {saveResult.kind === 'verify' && (
                <button
                  type="button"
                  onClick={() => void save(true)}
                  disabled={saving}
                  className="rounded-lg border border-amber-600 px-3 py-1 text-xs font-medium text-amber-800 transition-colors hover:bg-amber-100 disabled:opacity-50 dark:border-amber-500 dark:text-amber-300 dark:hover:bg-amber-900/40"
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
        {ADMIN_SECTIONS.map((section) => (
          <SettingsSectionCard
            key={section.id}
            section={section}
            values={draftValues}
            secrets={draftSecrets}
            secretSet={secretSet}
            secretSources={secretSources}
            secretCleared={Object.fromEntries(clearedSecrets.map((key) => [key, true]))}
            secretsUnreadable={view.secrets_unreadable}
            providers={view.llm_providers}
            onValueChange={onValueChange}
            onSecretChange={onSecretChange}
            onSecretClear={onSecretClear}
          />
        ))}
        <ProvidersPanel />
        <CorpusPanel />
        <MemoryPanel />
        <StatusPanel />
        <p className="pb-6 text-center text-xs text-ink-400">
          Grounding, citation validation, refusal, and prompt-injection protection are
          always on — this console changes how the system runs, never its guarantees.
        </p>
      </main>
    </div>
  )
}
