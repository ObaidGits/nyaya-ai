/**
 * Citation model + chip parsing (PRD §6.4, C-010).
 *
 * The backend emits inline citations in two shapes:
 *   [BNS s.103(1)]        — statute authority
 *   [Document <id> p.12]  — user-document evidence
 * Citations are parsed from the answer text and matched against the
 * `sources` payload the backend sent — the frontend never invents source
 * details (C-012/C-013: everything shown comes from the backend payload).
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

/** Attach backend sources to parsed citation labels. */
export function matchCitations(text: string, sources: Source[]): Citation[] {
  return parseCitations(text).map((label) => ({
    label,
    source: sources.find((s) => citationKey(s.citation) === citationKey(label)),
  }))
}
