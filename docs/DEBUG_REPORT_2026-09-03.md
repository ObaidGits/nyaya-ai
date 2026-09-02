# Production Debugging, Repair & Validation Report — 2026-09-03

Final production-grade pass over four reported regressions: (1) PDF RAG
broken, (2) BNS chat intermittent, (3) citations/drawer intermittently
empty, (4) suspected systemic state/caching/concurrency issue. Every fix
was reproduced live, root-caused, repaired, and verified against the
running stack (Docker Compose: api, worker, redis, qdrant, frontend).

## Summary of root causes (six, three of them stacked on PDF RAG)

| # | Defect | Root cause | Fix |
|---|--------|-----------|-----|
| RC1 | PDF-grounded questions routed to statute store | Keyword router had no legal-artifact nouns ("suit", "petition", "writ", "FIR", ...) | `backend/app/retrieval/intent.py` noun regex |
| RC2 | Empty citation drawer on subsection/document citations | Frontend matched citation label to source by exact string only | `frontend/src/lib/citations.ts` fallback matching |
| RC3 | BNS intermittency: valid answers intermittently replaced by refusal | Citation-check regeneration could refuse and overwrite a previously valid, cited answer | `backend/app/generation/service.py` preserves validated pre-regen answer |
| RC4 | Gemini API key written into httpx logs | Key passed as `?key=` URL query param; httpx logs URLs | `backend/app/llm/gemini.py` moves key to `x-goog-api-key` header (5 call sites) |
| RC5 | Retrieval missed exact-content document chunks | Pure dense retrieval; parties/title chunks scored low on embedding similarity | `backend/app/documents/retrieval.py` hybrid dense+lexical with RRF fusion |
| RC6 | Model refused document-sourced answers despite correct chunks | System prompt framed assistant as criminal-law-only | `backend/app/generation/prompt.py` evidence-driven framing |

Supporting change: statute-routed queries with INSUFFICIENT statute
evidence now fall through to the session's documents before failing
closed (`backend/app/retrieval/service.py`), with guardrails (weak docs
don't rescue; no session fails closed; statute-sufficient never falls
back; off-corpus statute questions stay refused).

## Fix verification (live, against the deployed stack)

- **PDF RAG (RC1+RC5+RC6 + fallback):** suit petition questions —
  "parties to the suit and amount claimed" (fresh session, freshly
  uploaded document), "filing date / cause of action", "documents to be
  produced under the notice" — all answered with `[Document <id> p.N]`
  inline citations and populated sources. Blank-field questions (fields
  left blank in the PDF itself) correctly refuse — honest behavior.
- **BNS chat (RC3):** 20/20 repeated "What is the punishment for
  murder?" — zero refusals, zero intermittent failures (previously
  1-in-25).
- **Citations/drawer (RC2):** frontend unit tests 119/119 (incl. new
  subsection→section and document-id matching cases); Playwright
  live-chat E2E 2/2 (grounded answer streams with citations; off-corpus
  question refuses).
- **Key exposure (RC4):** Gemini calls now carry the key in
  `x-goog-api-key`; request URL contains no key; regression test
  asserts header present + URL param absent.
- **Session isolation:** fresh session with no uploads receives zero
  document search hits and a grounded refusal — verified live.
- **Test suites:** backend 915 passed / 2 skipped (full suite incl. 15
  new regression tests); frontend 119 passed; TypeScript and ESLint
  clean.

## Honest limitations (not claimed fixed beyond evidence)

1. Stress depth: BNS murder question verified at 20 repeats, not
   unbounded. Regen-refusal preservation guarantees a *cited* answer
   survives regeneration, not that every phrasing of every question
   succeeds.
2. Cause-of-action answers: the suit PDF leaves parts of the prayer
   narrative sparse; the model answers what the evidence supports and
   refuses the blank remainder. Not a defect, but not a full answer
   either.
3. Combined BNS+document and prompt-injection-in-document cases are
   covered by unit/regression tests and the prompt's untrusted-evidence
   framing, but were not the focus of a dedicated live red-team session
   in this pass.
4. CI pipeline (GitHub Actions run) was not re-executed for this pass;
   verification ran locally against the rebuilt Docker stack.

## Test-fixture hygiene

Fixture PDFs were used only as temporary end-to-end inputs over HTTP.
No fixture filenames, contents, or metadata appear in production code;
no fixture PDFs were committed; no session IDs or API keys were
committed (diff scanned before each commit).

## Commits

Incremental, one per root cause, each with regression tests:
- `fix(retrieval): route legal-artifact questions to DOCUMENT evidence`
- `fix(documents): hybrid dense+lexical retrieval for session documents`
- `fix(retrieval): document fallback when statute evidence insufficient`
- `fix(generation): evidence-driven system prompt for document answers`
- `fix(generation): preserve validated answer when regeneration refuses`
- `fix(llm): move Gemini API key from URL query param to request header`
- `fix(frontend): citation matching for subsection and document citations`
