/**
 * Answer-language selector (multilingual support, D-077).
 *
 * A native <select> for full keyboard navigation and screen-reader
 * support. Manual selection overrides auto detection; "Auto detect"
 * answers Latin-script input in English and detects Indic scripts.
 */

import { LANGUAGE_OPTIONS } from '../lib/languages'

interface LanguageSelectorProps {
  language: string
  onChange: (language: string) => void
  disabled?: boolean
}

export function LanguageSelector({ language, onChange, disabled }: LanguageSelectorProps) {
  return (
    <div className="flex items-center">
      <label htmlFor="language-select" className="sr-only">
        Answer language
      </label>
      <select
        id="language-select"
        value={language}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        title="Language for answers. Manual choice overrides auto detection."
        className="max-w-[9.5rem] cursor-pointer rounded-full border border-ink-200 bg-white px-2.5 py-1 text-xs font-medium text-ink-700 outline-none transition-colors hover:border-ink-300 focus-visible:border-brand-500 focus-visible:ring-2 focus-visible:ring-brand-500/20 disabled:cursor-not-allowed disabled:opacity-50 sm:max-w-none dark:border-ink-800 dark:bg-ink-900 dark:text-ink-300 dark:hover:border-ink-700"
      >
        {LANGUAGE_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  )
}
