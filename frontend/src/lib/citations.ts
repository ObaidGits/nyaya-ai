/**
 * Citation model + chip parsing (PRD §6.4, C-010).
 *
 * The backend emits inline citations in two shapes:
 *   [BNS s.103(1)]        — statute authority
 *   [Document <id> p.12]  — user-document evidence
 * Citations are parsed from the answer text and matched against the
 * `sources` payload the backend sent — the frontend never invents source
 * details (C-012/C-013: everything shown comes from the backend payload).
 *
 * Matching is PREFIX-based, not exact: the answer may cite a subsection
 * ([BNS s.103(1)]) while the source payload carries the whole section
 * ([BNS s.103]) — the subsection is evidence-backed by that chunk (the
 * backend citation guard validates granularity), so the drawer must open
 * the section source rather than show "no source on record". Likewise a
 * document citation with a page ([Document <id> p.2]) matches the source
 * for the same document (chunk pages are in the payload fields).
 */

import type { Source } from '../types'

export interface Citation {
  label: string
  source: Source | undefined
}

const CITATION_RE = /\[([A-Za-z][^\][]{1,80}?)\]/g

/** Parse citation labels out of an answer string. */
export function parseCitations(text: string): string[] {
  const labels: string[] = []
  for (const match of text.matchAll(CITATION_RE)) {
    const label = match[1].trim()
    if (isCitationLabel(label) && !labels.includes(label)) labels.push(label)
  }
  return labels
}

function isCitationLabel(label: string): boolean {
  // [BNS s.103(1)] / [TS s.4] statute form, or [Document <id>] with optional
  // page part — the backend emits both [Document <id>] and [Document <id> p.2].
  return (
    /^[A-Za-z]{2,6}\s+s\.\d+/i.test(label) ||
    /^Document\s+\S+(\s+p\.\d+)?$/i.test(label)
  )
}

/** Bracket-insensitive key: backend citations may arrive wrapped in []. */
const citationKey = (label: string) => label.replace(/^\[|\]$/g, '').trim()

/**
 * Statute citation base: act + section without subsections.
 * "[BNS s.103(1)]" → "bns s.103" — subsection citations resolve to the
 * whole-section source chunk that covers them.
 */
function statuteBase(label: string): string | null {
  const match = label.match(/^([A-Za-z]{2,6})\s+s\.(\d+)/i)
  return match ? `${match[1].toLowerCase()} s.${match[2]}` : null
}

/** Document citation base: "[Document <id> p.2]" → "<id>". */
function documentBase(label: string): string | null {
  const match = label.match(/^Document\s+(\S+?)(\s+p\.\d+)?$/i)
  return match ? match[1] : null
}

/** Attach backend sources to parsed citation labels. */
export function matchCitations(text: string, sources: Source[]): Citation[] {
  return parseCitations(text).map((label) => {
    const key = citationKey(label)
    let source = sources.find((s) => citationKey(s.citation) === key)

    if (!source) {
      // Subsection statute citation → whole-section source:
      // [BNS s.103(1)] resolves against the [BNS s.103] payload entry.
      const base = statuteBase(key)
      if (base) {
        source = sources.find((s) => statuteBase(citationKey(s.citation)) === base)
      }
    }

    if (!source) {
      // Document citation (with or without page) → any source entry for
      // the same document id; the payload carries page_start/page_end.
      const docId = documentBase(key)
      if (docId) {
        source = sources.find(
          (s) =>
            s.source_type === 'user_document' &&
            documentBase(citationKey(s.citation)) === docId,
        )
      }
    }

    return { label, source }
  })
}
