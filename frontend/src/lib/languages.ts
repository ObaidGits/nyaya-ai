/**
 * Language preference (multilingual support, D-077).
 *
 * The preference is one of "auto" (detect from the message; Latin-script
 * input answers in English) or a supported Indian-language code. It is
 * persisted in localStorage next to the theme and sent with every chat
 * request as the `language` field.
 */

export interface LanguageOption {
  value: string
  label: string
}

/** Selector options: Auto detect + English + 11 supported Indic languages. */
export const LANGUAGE_OPTIONS: readonly LanguageOption[] = [
  { value: 'auto', label: 'Auto detect' },
  { value: 'en', label: 'English' },
  { value: 'hi', label: 'हिन्दी — Hindi' },
  { value: 'bn', label: 'বাংলা — Bengali' },
  { value: 'mr', label: 'मराठी — Marathi' },
  { value: 'gu', label: 'ગુજરાતી — Gujarati' },
  { value: 'ta', label: 'தமிழ் — Tamil' },
  { value: 'te', label: 'తెలుగు — Telugu' },
  { value: 'kn', label: 'ಕನ್ನಡ — Kannada' },
  { value: 'ml', label: 'മലയാളം — Malayalam' },
  { value: 'pa', label: 'ਪੰਜਾਬੀ — Punjabi' },
  { value: 'or', label: 'ଓଡ଼ିଆ — Odia' },
  { value: 'as', label: 'অসমীয়া — Assamese' },
] as const

export const DEFAULT_LANGUAGE = 'auto'

const STORAGE_KEY = 'nyaya.language'

const VALID = new Set(LANGUAGE_OPTIONS.map((option) => option.value))

export function isValidLanguage(value: string): boolean {
  return VALID.has(value)
}

/**
 * Load the persisted preference. Default is "auto": Latin-script messages
 * answer in English (the pre-existing behavior), while Indic-script
 * messages are detected and answered in their own language.
 */
export function loadLanguage(): string {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored && isValidLanguage(stored)) return stored
  } catch {
    // localStorage unavailable (private mode): fall through to default.
  }
  return DEFAULT_LANGUAGE
}

export function saveLanguage(value: string): void {
  if (!isValidLanguage(value)) return
  try {
    localStorage.setItem(STORAGE_KEY, value)
  } catch {
    // Ignore write failures; the preference simply won't persist.
  }
}
