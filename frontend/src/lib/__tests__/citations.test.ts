import { describe, expect, it } from 'vitest'
import { matchCitations, parseCitations } from '../citations'
import type { Source } from '../../types'

describe('parseCitations', () => {
  it('extracts statute citations with subsections', () => {
    expect(parseCitations('Murder is punishable [BNS s.103(1)] as stated.')).toEqual([
      'BNS s.103(1)',
    ])
  })

  it('extracts document citations', () => {
    expect(parseCitations('See [Document abc123 p.4].')).toEqual(['Document abc123 p.4'])
  })

  it('deduplicates and ignores non-citation brackets', () => {
    const text = '[BNS s.103] and again [BNS s.103], but not [note] or [101]'
    expect(parseCitations(text)).toEqual(['BNS s.103'])
  })
})

describe('matchCitations', () => {
  const sources: Source[] = [
    { citation: 'BNS s.103(1)', text: 'Whoever commits murder…', page_start: 45, page_end: 45 },
  ]

  it('attaches matching backend sources', () => {
    const [citation] = matchCitations('Answer [BNS s.103(1)].', sources)
    expect(citation.source?.text).toContain('murder')
  })

  it('leaves unmatched citations without invented data', () => {
    const [citation] = matchCitations('Answer [BNS s.999].', sources)
    expect(citation.source).toBeUndefined()
  })
})
