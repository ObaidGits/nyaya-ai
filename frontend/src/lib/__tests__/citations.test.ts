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

  describe('subsection citation resolves to whole-section source (live regression)', () => {
    // The backend validates granularity itself: an answer citing
    // [BNS s.103(1)] is backed by the whole-section chunk sent as
    // [BNS s.103]. The drawer previously showed "no source on record"
    // for every subsection citation.
    const sectionSources: Source[] = [
      { citation: 'BNS s.103', text: 'Whoever commits murder…', page_start: 44, page_end: 44 },
    ]

    it('matches [BNS s.103(1)] against the [BNS s.103] payload entry', () => {
      const [citation] = matchCitations(
        'Answer cites the subsection [BNS s.103(1)].',
        sectionSources,
      )
      expect(citation.source?.text).toContain('murder')
    })

    it('matches [BNS s.103] exactly (unchanged behavior)', () => {
      const [citation] = matchCitations('Answer [BNS s.103].', sectionSources)
      expect(citation.source?.citation).toBe('BNS s.103')
    })

    it('does not match a different section that shares the act', () => {
      const [citation] = matchCitations('Answer [BNS s.104].', sectionSources)
      expect(citation.source).toBeUndefined()
    })
  })

  describe('document citations resolve by document id (live regression)', () => {
    const docSources: Source[] = [
      {
        citation: 'Document d31f9c',
        source_type: 'user_document',
        document_id: 'd31f9c',
        text: 'Legal notice text…',
        page_start: 2,
        page_end: 2,
      },
    ]

    it('matches [Document <id> p.2] to the document source', () => {
      const [citation] = matchCitations('Answer [Document d31f9c p.2].', docSources)
      expect(citation.source?.document_id).toBe('d31f9c')
      expect(citation.source?.text).toContain('notice')
    })

    it('matches [Document <id>] without a page', () => {
      const [citation] = matchCitations('Answer [Document d31f9c].', docSources)
      expect(citation.source?.document_id).toBe('d31f9c')
    })

    it('does not match a different document id', () => {
      const [citation] = matchCitations('Answer [Document other99].', docSources)
      expect(citation.source).toBeUndefined()
    })
  })
})
