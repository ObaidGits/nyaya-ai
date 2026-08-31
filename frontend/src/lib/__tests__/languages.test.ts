import { beforeEach, describe, expect, it } from 'vitest'
import {
  DEFAULT_LANGUAGE,
  LANGUAGE_OPTIONS,
  isValidLanguage,
  loadLanguage,
  saveLanguage,
} from '../languages'

beforeEach(() => {
  localStorage.clear()
})

describe('language preference', () => {
  it('defaults to auto (English for Latin-script input)', () => {
    expect(loadLanguage()).toBe('auto')
    expect(DEFAULT_LANGUAGE).toBe('auto')
  })

  it('offers Auto detect, English, and the 11 supported Indic languages', () => {
    const values = LANGUAGE_OPTIONS.map((option) => option.value)
    expect(values[0]).toBe('auto')
    expect(values[1]).toBe('en')
    expect(values).toHaveLength(13)
    for (const code of ['hi', 'bn', 'mr', 'gu', 'ta', 'te', 'kn', 'ml', 'pa', 'or', 'as']) {
      expect(values).toContain(code)
    }
  })

  it('round-trips a saved preference', () => {
    saveLanguage('hi')
    expect(loadLanguage()).toBe('hi')
    expect(localStorage.getItem('nyaya.language')).toBe('hi')
  })

  it('ignores stored values that are not supported codes', () => {
    localStorage.setItem('nyaya.language', 'fr')
    expect(loadLanguage()).toBe('auto')
  })

  it('rejects unsupported codes on save', () => {
    saveLanguage('fr')
    expect(localStorage.getItem('nyaya.language')).toBeNull()
  })

  it('validates codes without touching storage', () => {
    expect(isValidLanguage('auto')).toBe(true)
    expect(isValidLanguage('as')).toBe(true)
    expect(isValidLanguage('fr')).toBe(false)
    expect(isValidLanguage('')).toBe(false)
  })
})
