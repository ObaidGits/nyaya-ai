/** Secret input with show/hide masking; never displays the stored value. */

import { useState } from 'react'
import type { SettingField } from '../../lib/adminSchema'

interface Props {
  field: SettingField
  value: string
  secretSet: boolean
  onChange: (value: string) => void
}

export function SecretField({ field, value, secretSet, onChange }: Props) {
  const [reveal, setReveal] = useState(false)
  return (
    <div>
      <label className="block text-sm font-medium" htmlFor={`set-${field.key}`}>
        {field.label}
      </label>
      <div className="mt-1 flex gap-2">
        <input
          id={`set-${field.key}`}
          type={reveal ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={secretSet ? '•••••••• (set — leave blank to keep)' : field.placeholder ?? 'Not set'}
          autoComplete="off"
          className="min-w-0 flex-1 rounded-lg border border-ink-300 bg-white px-3 py-2 font-mono text-sm outline-none transition-colors focus:border-brand-600 focus:ring-2 focus:ring-brand-600/30 dark:border-ink-700 dark:bg-ink-950"
        />
        <button
          type="button"
          onClick={() => setReveal((r) => !r)}
          aria-label={reveal ? 'Hide API key' : 'Show API key'}
          aria-pressed={reveal}
          className="shrink-0 rounded-lg border border-ink-300 px-3 text-sm text-ink-600 transition-colors hover:bg-ink-100 dark:border-ink-700 dark:text-ink-300 dark:hover:bg-ink-800"
        >
          {reveal ? 'Hide' : 'Show'}
        </button>
      </div>
      {secretSet && !value && (
        <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">A key is configured server-side.</p>
      )}
    </div>
  )
}
