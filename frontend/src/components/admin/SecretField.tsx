/** Secret input with show/hide masking; never displays the stored value. */

import { useState } from 'react'
import type { SettingField } from '../../lib/adminSchema'

interface Props {
  field: SettingField
  value: string
  secretSet: boolean
  /** "env" | "console" | "" — env values are bootstrap defaults; a key
   * saved in the console wins (D-090). */
  source?: string
  /** Pending explicit removal (applied on Save). */
  cleared?: boolean
  onChange: (value: string) => void
  /** Toggle the pending removal (also discards any typed draft). */
  onClear?: () => void
}

export function SecretField({ field, value, secretSet, source, cleared, onChange, onClear }: Props) {
  const [reveal, setReveal] = useState(false)
  const removable = Boolean(onClear) && secretSet && !cleared
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
          placeholder={
            cleared
              ? '•••••••• (will be removed on save)'
              : secretSet
                ? '•••••••• (set — leave blank to keep)'
                : (field.placeholder ?? 'Not set')
          }
          autoComplete="off"
          disabled={cleared}
          className="min-w-0 flex-1 rounded-lg border border-ink-300 bg-white px-3 py-2 font-mono text-sm outline-none transition-colors focus:border-brand-600 focus:ring-2 focus:ring-brand-600/30 disabled:opacity-60 dark:border-ink-700 dark:bg-ink-950"
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
        {removable && (
          <button
            type="button"
            onClick={onClear}
            aria-label={`Remove ${field.label}`}
            className="shrink-0 rounded-lg border border-red-300 px-3 text-sm text-red-700 transition-colors hover:bg-red-50 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950/40"
          >
            Remove
          </button>
        )}
      </div>
      {cleared && onClear && (
        <p
          role="status"
          className="mt-1 flex items-center justify-between gap-2 rounded bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:bg-amber-950/60 dark:text-amber-300"
        >
          <span>
            Key will be removed when you save changes.
            {source === 'env' ? ' The environment value becomes active again.' : ''}
          </span>
          <button type="button" onClick={onClear} className="underline">
            Undo
          </button>
        </p>
      )}
      {!cleared && source === 'env' && (
        <p className="mt-1 rounded bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:bg-amber-950/60 dark:text-amber-300">
          A key is set in the server environment ({field.key.toUpperCase()}) as the
          bootstrap default — saving a key here overrides it (the saved key wins).
        </p>
      )}
      {!cleared && source !== 'env' && secretSet && !value && (
        <p className="mt-1 text-xs text-ink-500 dark:text-ink-400">
          A key is saved in the admin console and used by the server.
        </p>
      )}
    </div>
  )
}
