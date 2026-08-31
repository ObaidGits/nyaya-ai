/** One settings card: fields for a schema section plus its Test button. */

import { useState } from 'react'
import {
  fieldVisible,
  type SettingField,
  type SettingSection,
} from '../../lib/adminSchema'
import { testConnection, type LlmProviderInfo, type TestResult } from '../../lib/admin'
import { SecretField } from './SecretField'

type Values = Record<string, string | number | boolean>
type Secrets = Record<string, string>

interface Props {
  section: SettingSection
  values: Values
  secrets: Secrets
  secretSet: Record<string, boolean>
  providers: LlmProviderInfo[]
  onValueChange: (key: string, value: string | number | boolean) => void
  onSecretChange: (key: string, value: string) => void
}

const inputClass =
  'mt-1 w-full rounded-lg border border-ink-300 bg-white px-3 py-2 text-sm outline-none transition-colors focus:border-brand-600 focus:ring-2 focus:ring-brand-600/30 dark:border-ink-700 dark:bg-ink-950'

function NumberField({ field, value, onChange }: {
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

function SelectField({ field, value, onChange, options }: {
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

function BooleanField({ field, value, onChange }: {
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
  providers,
  onValueChange,
  onSecretChange,
}: Props) {
  const [test, setTest] = useState<TestResult | null>(null)
  const [testing, setTesting] = useState(false)

  const runTest = async () => {
    setTesting(true)
    setTest(null)
    try {
      setTest(await testConnection(section.test!.kind))
    } catch {
      setTest({ success: false, latency_ms: null, message: 'Test request failed.' })
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
            if (field.kind === 'secret') {
              return (
                <SecretField
                  key={field.key}
                  field={field}
                  value={secrets[field.key] ?? ''}
                  secretSet={secretSet[field.key] ?? false}
                  onChange={(v) => onSecretChange(field.key, v)}
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
                </div>
              )
            }
            return (
              <div key={field.key} className={field.kind === 'text' && field.key.includes('url') ? 'sm:col-span-2' : ''}>
                <label className="block text-sm font-medium" htmlFor={`set-${field.key}`}>
                  {field.label}
                </label>
                {field.kind === 'number' && (
                  <NumberField
                    field={field}
                    value={typeof values[field.key] === 'number' ? (values[field.key] as number) : Number(values[field.key] ?? 0)}
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
                    options={providers.map((p) => ({ value: p.name, label: p.label }))}
                    onChange={(v) => onValueChange(field.key, v)}
                  />
                )}
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
