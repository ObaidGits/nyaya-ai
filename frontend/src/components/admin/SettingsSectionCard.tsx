/** One settings card: fields for a schema section plus its Test button. */

import { useState } from 'react'
import { fieldVisible, type SettingField, type SettingSection } from '../../lib/adminSchema'
import {
  fetchLlmModels,
  testConnection,
  testLlmConnection,
  type LlmDraftConfig,
  type LlmProviderInfo,
  type TestResult,
} from '../../lib/admin'
import { SecretField } from './SecretField'

type Values = Record<string, string | number | boolean>
type Secrets = Record<string, string>

interface Props {
  section: SettingSection
  values: Values
  secrets: Secrets
  secretSet: Record<string, boolean>
  secretSources: Record<string, string>
  /** Where each editable setting's effective value comes from: "env" | "console". */
  valueSources: Record<string, string>
  /** Keys with a pending explicit removal. */
  secretCleared: Record<string, boolean>
  /** Secret fields whose stored ciphertext can't be decrypted server-side. */
  secretsUnreadable?: string[]
  providers: LlmProviderInfo[]
  onValueChange: (key: string, value: string | number | boolean) => void
  onSecretChange: (key: string, value: string) => void
  onSecretClear: (key: string) => void
}

const inputClass =
  'mt-1 w-full rounded-lg border border-ink-300 bg-white px-3 py-2 text-sm outline-none transition-colors focus:border-brand-600 focus:ring-2 focus:ring-brand-600/30 dark:border-ink-700 dark:bg-ink-950'

/** Console-saved settings override the environment default (D-090); the
 * env default is silent so only real overrides are surfaced. */
function SourceHint({ source }: { source: string }) {
  if (source !== 'console') return null
  return (
    <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
      Saved in the admin console — overrides the environment default.
    </p>
  )
}

function NumberField({
  field,
  value,
  onChange,
}: {
  field: SettingField
  value: string | number
  onChange: (v: number) => void
}) {
  return (
    <input
      id={`set-${field.key}`}
      type="number"
      value={String(value)}
      min={field.min}
      max={field.max}
      step={field.step}
      onChange={(e) => onChange(Number(e.target.value))}
      className={inputClass}
    />
  )
}

function SelectField({
  field,
  value,
  onChange,
  options,
}: {
  field: SettingField
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <select
      id={`set-${field.key}`}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={inputClass}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )
}

function BooleanField({
  field,
  value,
  onChange,
}: {
  field: SettingField
  value: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <label className="mt-2 flex items-center gap-2 text-sm">
      <input
        id={`set-${field.key}`}
        type="checkbox"
        checked={value}
        onChange={(e) => onChange(e.target.checked)}
        className="size-4 accent-brand-600"
      />
      {field.label}
    </label>
  )
}

export function SettingsSectionCard({
  section,
  values,
  secrets,
  secretSet,
  secretSources,
  valueSources,
  secretCleared,
  secretsUnreadable,
  providers,
  onValueChange,
  onSecretChange,
  onSecretClear,
}: Props) {
  const [test, setTest] = useState<TestResult | null>(null)
  const [testing, setTesting] = useState(false)
  // LLM model combobox state (loaded from the provider's model list).
  const [models, setModels] = useState<string[]>([])
  const [modelsMessage, setModelsMessage] = useState<string | null>(null)
  const [loadingModels, setLoadingModels] = useState(false)
  // Dropdown visibility for the model list (datalist is unreliable — some
  // browsers only surface it after typing, so users never see the options).
  const [modelsOpen, setModelsOpen] = useState(false)
  // Base-URL override: providers with a fixed API URL hide the field until
  // the admin explicitly opts into a custom endpoint.
  const [overrideUrl, setOverrideUrl] = useState(false)

  const provider = providers.find((p) => p.name === String(values.llm_provider ?? ''))
  const urlRequired = provider?.requires_base_url ?? false

  // Provider switch: drop the custom-URL override and the previous provider's
  // loaded model list — both would be stale for the new provider. Adjusted
  // during render (React's documented pattern for reacting to prop changes)
  // rather than in an effect, so no cascading render is triggered.
  const activeProvider = String(values.llm_provider ?? '')
  const [lastProvider, setLastProvider] = useState(activeProvider)
  if (lastProvider !== activeProvider) {
    setLastProvider(activeProvider)
    setOverrideUrl(false)
    setModels([])
    setModelsMessage(null)
    setModelsOpen(false)
  }

  /** The config currently in the form — Test/Load must exercise this, not
   * the last saved state (a typed API key wins over the stored one). */
  const draftConfig = (): LlmDraftConfig => ({
    provider: String(values.llm_provider ?? ''),
    model: String(values.llm_model ?? ''),
    base_url: String(values.llm_base_url ?? ''),
    api_key: secrets.llm_api_key ?? '',
  })

  const loadModels = async () => {
    setLoadingModels(true)
    setModelsMessage(null)
    try {
      const result = await fetchLlmModels(draftConfig())
      setModels(result.models)
      // Show the list immediately — the whole point of loading it.
      setModelsOpen(result.models.length > 0)
      setModelsMessage(
        result.models.length > 0
          ? `${result.models.length} models loaded from ${result.provider}.`
          : 'The provider returned no models.',
      )
    } catch (error) {
      setModelsMessage(error instanceof Error ? error.message : 'Could not load models.')
    } finally {
      setLoadingModels(false)
    }
  }

  const runTest = async () => {
    setTesting(true)
    setTest(null)
    try {
      setTest(
        section.test!.kind === 'llm'
          ? await testLlmConnection(draftConfig())
          : await testConnection(section.test!.kind),
      )
    } catch {
      setTest({
        success: false,
        latency_ms: null,
        message: 'Test request failed.',
      })
    } finally {
      setTesting(false)
    }
  }

  return (
    <section
      aria-labelledby={`sec-${section.id}`}
      className="rounded-xl border border-ink-200 bg-white p-5 shadow-sm dark:border-ink-800 dark:bg-ink-900"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id={`sec-${section.id}`} className="font-serif text-lg font-bold">
            {section.title}
          </h2>
          <p className="mt-1 max-w-2xl text-sm text-ink-500 dark:text-ink-400">
            {section.description}
          </p>
        </div>
        {section.test && (
          <button
            type="button"
            onClick={runTest}
            disabled={testing}
            className="rounded-lg border border-brand-600 px-3 py-1.5 text-sm font-medium text-brand-700 transition-colors hover:bg-brand-50 disabled:opacity-50 dark:border-brand-400 dark:text-brand-300 dark:hover:bg-brand-950/40"
          >
            {testing ? 'Testing…' : section.test.label}
          </button>
        )}
      </div>

      {test && (
        <p
          role="status"
          className={`mt-3 rounded-lg px-3 py-2 text-sm ${
            test.success
              ? 'bg-emerald-50 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300'
              : 'bg-red-50 text-red-700 dark:bg-red-950/60 dark:text-red-300'
          }`}
        >
          {test.message}
          {test.latency_ms !== null && ` (${test.latency_ms} ms)`}
        </p>
      )}

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        {section.fields
          .filter((field) => fieldVisible(field, values))
          .map((field) => {
            // LLM base URL: hidden unless the provider has no fixed API URL
            // (or the admin opts into a custom endpoint).
            if (field.key === 'llm_base_url' && section.id === 'llm') {
              if (provider && !urlRequired && !overrideUrl) {
                return (
                  <div key={field.key} className="sm:col-span-2">
                    <label className="mt-1 flex items-center gap-2 text-xs text-ink-500 dark:text-ink-400">
                      <input
                        type="checkbox"
                        checked={false}
                        onChange={() => setOverrideUrl(true)}
                        className="size-4 accent-brand-600"
                      />
                      Use a custom API URL instead of the provider default
                      {provider.default_base_url ? ` (${provider.default_base_url})` : ''}
                    </label>
                  </div>
                )
              }
              return (
                <div key={field.key} className="sm:col-span-2">
                  <label className="block text-sm font-medium" htmlFor={`set-${field.key}`}>
                    {field.label}
                    {urlRequired && <span className="text-red-600"> *</span>}
                  </label>
                  <input
                    id={`set-${field.key}`}
                    type="text"
                    value={String(values[field.key] ?? '')}
                    placeholder={provider?.default_base_url || field.placeholder}
                    onChange={(e) => onValueChange(field.key, e.target.value)}
                    className={inputClass}
                  />
                  <SourceHint source={valueSources[field.key] ?? ''} />
                  {field.help && (
                    <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">{field.help}</p>
                  )}
                </div>
              )
            }
            // LLM model: combobox — pick from the provider's list or type any id.
            if (field.key === 'llm_model' && section.id === 'llm') {
              return (
                <div key={field.key} className="sm:col-span-2">
                  <div className="flex items-center justify-between">
                    <label className="block text-sm font-medium" htmlFor={`set-${field.key}`}>
                      {field.label}
                    </label>
                    <button
                      type="button"
                      onClick={loadModels}
                      disabled={loadingModels}
                      className="rounded-lg border border-brand-600 px-2.5 py-1 text-xs font-medium text-brand-700 transition-colors hover:bg-brand-50 disabled:opacity-50 dark:border-brand-400 dark:text-brand-300 dark:hover:bg-brand-950/40"
                    >
                      {loadingModels ? 'Loading…' : 'Load models'}
                    </button>
                  </div>
                  <div className="relative mt-1">
                    <input
                      id={`set-${field.key}`}
                      type="text"
                      value={String(values[field.key] ?? '')}
                      placeholder={provider?.default_model || field.placeholder}
                      onChange={(e) => onValueChange(field.key, e.target.value)}
                      onFocus={() => models.length > 0 && setModelsOpen(true)}
                      onBlur={() => window.setTimeout(() => setModelsOpen(false), 150)}
                      className={inputClass}
                    />
                    {models.length > 0 && (
                      <button
                        type="button"
                        aria-label={modelsOpen ? 'Hide model list' : 'Show model list'}
                        aria-expanded={modelsOpen}
                        onClick={() => setModelsOpen((open) => !open)}
                        className="absolute inset-y-0 right-2 flex items-center px-1 text-ink-400 transition-colors hover:text-ink-700 dark:hover:text-ink-200"
                      >
                        <span
                          className={`inline-block transition-transform ${modelsOpen ? 'rotate-180' : ''}`}
                        >
                          ▾
                        </span>
                      </button>
                    )}
                    {modelsOpen && models.length > 0 && (
                      <ul
                        role="listbox"
                        aria-label="Available models"
                        className="absolute z-20 mt-1 max-h-60 w-full overflow-auto rounded-lg border border-ink-300 bg-white py-1 shadow-lg dark:border-ink-700 dark:bg-ink-950"
                      >
                        {models.map((model) => (
                          <li key={model}>
                            <button
                              type="button"
                              role="option"
                              aria-selected={model === String(values[field.key] ?? '')}
                              onMouseDown={(e) => e.preventDefault()} // keep input focus
                              onClick={() => {
                                onValueChange(field.key, model)
                                setModelsOpen(false)
                              }}
                              className={`block w-full px-3 py-1.5 text-left font-mono text-xs transition-colors hover:bg-brand-50 dark:hover:bg-ink-800 ${
                                model === String(values[field.key] ?? '')
                                  ? 'bg-brand-50 font-semibold text-brand-800 dark:bg-ink-800 dark:text-brand-300'
                                  : 'text-ink-700 dark:text-ink-300'
                              }`}
                            >
                              {model}
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  {modelsMessage && (
                    <p role="status" className="mt-1 text-xs text-ink-500 dark:text-ink-400">
                      {modelsMessage}
                    </p>
                  )}
                  <SourceHint source={valueSources[field.key] ?? ''} />
                  {field.help && (
                    <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">{field.help}</p>
                  )}
                </div>
              )
            }
            if (field.kind === 'secret') {
              return (
                <SecretField
                  key={field.key}
                  field={field}
                  value={secrets[field.key] ?? ''}
                  secretSet={secretSet[field.key] ?? false}
                  source={secretSources[field.key] ?? ''}
                  cleared={Boolean(secretCleared[field.key])}
                  unreadable={secretsUnreadable?.includes(field.key)}
                  onChange={(v) => onSecretChange(field.key, v)}
                  onClear={() => onSecretClear(field.key)}
                />
              )
            }
            if (field.kind === 'boolean') {
              return (
                <div key={field.key} className="sm:col-span-2">
                  <BooleanField
                    field={field}
                    value={Boolean(values[field.key])}
                    onChange={(v) => onValueChange(field.key, v)}
                  />
                  <SourceHint source={valueSources[field.key] ?? ''} />
                </div>
              )
            }
            return (
              <div
                key={field.key}
                className={
                  field.kind === 'text' && field.key.includes('url') ? 'sm:col-span-2' : ''
                }
              >
                <label className="block text-sm font-medium" htmlFor={`set-${field.key}`}>
                  {field.label}
                </label>
                {field.kind === 'number' && (
                  <NumberField
                    field={field}
                    value={
                      typeof values[field.key] === 'number'
                        ? (values[field.key] as number)
                        : Number(values[field.key] ?? 0)
                    }
                    onChange={(v) => onValueChange(field.key, v)}
                  />
                )}
                {field.kind === 'text' && (
                  <input
                    id={`set-${field.key}`}
                    type="text"
                    value={String(values[field.key] ?? '')}
                    placeholder={field.placeholder}
                    onChange={(e) => onValueChange(field.key, e.target.value)}
                    className={inputClass}
                  />
                )}
                {field.kind === 'select' && field.options && (
                  <SelectField
                    field={field}
                    value={String(values[field.key] ?? field.options[0].value)}
                    options={field.options}
                    onChange={(v) => onValueChange(field.key, v)}
                  />
                )}
                {field.kind === 'provider-select' && (
                  <SelectField
                    field={field}
                    value={String(values[field.key] ?? 'ollama')}
                    options={providers.map((p) => ({
                      value: p.name,
                      label: p.label,
                    }))}
                    onChange={(v) => onValueChange(field.key, v)}
                  />
                )}
                <SourceHint source={valueSources[field.key] ?? ''} />
                {field.help && (
                  <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">{field.help}</p>
                )}
              </div>
            )
          })}
      </div>
    </section>
  )
}
