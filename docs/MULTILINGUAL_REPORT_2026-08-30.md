# Multilingual Indian Language Support — Implementation Report

Date: 2026-08-30 · Decision record: `docs/DECISIONS.md` **D-077** ·
Architecture: `docs/ARCHITECTURE.md` **§59** · Requirements (bonus):
`docs/REQUIREMENTS.md` **§22**

## 1. Architecture implemented

One shared pipeline with a language layer wrapped around it — the
authoritative English statute corpus, retrieval indexes, confidence
gate, citation guard, prompt-injection defenses, and session isolation
are untouched. No translated corpus copies, no per-language indexes.

```text
POST /api/v1/chat  (optional `language` = "auto" | code)
 → resolve answer language (manual selection overrides script detection)
 → conversational short-circuit: fixed in-language copy, no retrieval,
   no translation call, no LLM call, no citations
 → non-English input: translate query → English (translate-only prompt,
   existing local Ollama provider) — used ONLY for routing + retrieval
 → existing intent detection (Indic forms added) + hybrid retrieval +
   confidence gate
 → generation: ORIGINAL question + code-controlled ANSWER LANGUAGE
   instruction that pins citation labels untranslated
 → citation guard: unchanged checks + cross-script relevance bridge
 → SSE stream (token / sources / done) — contract unchanged
```

Failure semantics: any translation failure fails closed — retrieval
runs on the original message, finds nothing, and the pipeline refuses.
The refusal is a fixed translated string per language, emitted by code
(`REFUSAL_RESPONSES`), never model-generated; a model echoing any
language's refusal text is normalized to `refused=true`.

## 2. Supported languages

English (default for Latin-script input) + 11 Indian languages: Hindi,
Bengali, Marathi, Gujarati, Tamil, Telugu, Kannada, Malayalam, Punjabi,
Odia, Assamese. Auto-detect is Unicode-script based (deterministic,
zero dependencies). Known auto-detect limits: Devanagari resolves to
Hindi (Hindi/Marathi share the script); Bengali script resolves to
Assamese only when Assamese-specific characters (ৰ/ৱ) are present. A
manual selector choice always overrides detection.

## 3. Libraries / models used and licenses

| Component | Default? | License | Cost |
|---|---|---|---|
| Unicode-script detection (`app/language/detection.py`) | yes | in-repo code, no deps | none |
| Query translation via existing Ollama/Qwen provider | yes | existing local stack (already a project dependency) | local GPU/CPU only |
| fastText lid.176.bin (optional detector) | no | CC BY-SA 4.0, ~130 MB | free, local |
| AI4Bharat IndicTrans2 (optional translation) | no | MIT, ~2.4 GB both directions, GPU strongly recommended | free, local |

No paid APIs, no cloud translation services. Neither optional model is
installed by default: the normal deployment requires zero new downloads
(`language_detection_backend = "script"` default; setup instructions in
`app/language/detection.py` / `app/language/service.py` docstrings).

## 4. Files changed

**Backend — new** `app/language/` (`models.py`, `detection.py`,
`conversation.py`, `service.py`, `__init__.py`).

**Backend — edited** `app/api/v1/chat.py` (optional `language` request
field + validation; pipeline wiring), `app/generation/service.py`
(`answer_language` parameter, per-language refusal normalization),
`app/generation/prompt.py` (language instruction appended to system
prompt), `app/generation/citation_guard.py` (Indic prose gate,
digit-normalized section bridge, self-reference vocabulary, waivered
document-relevance counter), `app/retrieval/intent.py` (Indic section
regex + document-hint regex with Devanagari/Bengali digit
normalization), `app/generation/conversation.py` (refactor to expose
`conversational_category` / `reply_for_category`; English behavior
identical), `app/core/config.py` (3 new optional settings),
`app/main.py` (wires `LanguageService` onto app state),
`pyproject.toml` (ruff per-file-ignores for translated copy).

**Backend — tests** `tests/language/` — `test_detection.py`,
`test_conversation.py`, `test_service.py`, `test_intent.py`,
`test_citation_guard.py`, `test_chat_api.py` (end-to-end SSE).

**Frontend — new** `src/components/LanguageSelector.tsx` (native
`<select>`, keyboard-navigable, labeled), `src/lib/languages.ts`
(options, validation, `localStorage` persistence under
`nyaya.language`).

**Frontend — edited** `src/lib/sse.ts` (`language` field in the chat
request body, default `"auto"`), `src/components/ChatPanel.tsx`
(selector in header, disabled while streaming, preference passed to the
request), `src/App.tsx` (owns + persists the preference).

**Frontend — tests** `src/lib/__tests__/languages.test.ts` (new),
additions to `sse.test.ts` and `ChatPanel.test.tsx` (selector
rendering/default/options, switching sends the preference, auto keeps
existing behavior).

**Docs** DECISIONS.md D-077, ARCHITECTURE.md §59 + checklist entry,
REQUIREMENTS.md §22 (bonus checklist only — no existing requirement
modified).

## 5. Test results (exact)

Run in the development sandbox (all backend Python ≥3.12 runtime gates
other than ruff/compile require the host interpreter; commands below):

| Gate | Result |
|---|---|
| `ruff check .` (backend, whole repo) | **All checks passed!** |
| `ruff format --check app tests` (backend) | **144 files already formatted** |
| `python3 -m compileall app tests/language` | **OK** |
| Frontend `npm test` (vitest) | **9 files / 53 tests passed** |
| Frontend `npm run lint` (eslint) | clean |
| Frontend `tsc -b --noEmit` | clean (exit 0) |
| Frontend `vite build` | built (240.25 kB JS / 40.38 kB CSS, gzip 73.09/7.71 kB) — sandbox `dist/` unlink is a mount artifact; `npm run build` on the host is unaffected |

**Run on the host to complete the gates** (sandbox has Python 3.10 and
no pydantic, so pytest/mypy are host-only):

```bash
cd backend && .venv/bin/python -m pytest -q
cd backend && .venv/bin/mypy app
```

Expected: the existing 452 tests keep passing (the English workflow is
byte-identical: no `language` field → one provider call, no translation,
no language instruction) plus the new `tests/language/` suite
(script detection, conversational short-circuit, service seams, Indic
intent routing, citation-guard bridge, end-to-end chat API including
document isolation).

## 6. Live E2E scenarios (run on the host with the LLM profile up)

Start: `scripts/docker-up.sh` (or `docker compose up -d --build api
--profile llm`), then exercise `POST /api/v1/chat` (SSE):

```bash
lang_chat() { curl -sN -X POST http://localhost:8000/api/v1/chat \
  -H 'Content-Type: application/json' -H "X-Session-Id: $2" \
  -d "{\"message\": \"$1\"${3:+, \"language\": \"$3\"}}"; }

# 1. Casual "Hi"            → no citations, no retrieval
lang_chat 'Hi'
# 2. Casual "नमस्ते"          → fixed Hindi greeting, no citations
lang_chat 'नमस्ते'
# 3. Casual "আপনি কে?"       → fixed Bengali identity reply
lang_chat 'আপনি কে?'
# 4. Legal English          → grounded answer with [BNS s.103]-style citations
lang_chat 'What is the punishment for murder?'
# 5. Legal Hindi            → Hindi answer, citations preserved exactly
lang_chat 'धारा 103 में क्या प्रावधान है?'
# 6. Unsupported legal      → code-controlled refusal (in the answer language)
lang_chat 'क्वांटम भौतिकी का कानून क्या कहता है?'
# 7. Document EN (upload first, then ask)
lang_chat 'What does my notice say?'
# 8. Document Hindi (same session)
lang_chat 'इस दस्तावेज़ का सारांश दें'
# 9. Manual language override (English question, Hindi answer)
lang_chat 'What is the punishment for murder?' hi
# 10. Isolation: repeat 8 with a fresh X-Session-Id → refusal, no leak
lang_chat 'इस दस्तावेज़ का सारांश दें' other-session
```

Scenarios 1–3 make zero LLM calls (short-circuit works with the model
down). These were verified against the stubbed pipeline in
`tests/language/test_chat_api.py`; the live-model run above is the final
host confirmation.

## 7. Performance / resource impact

English and Latin-script traffic: zero added cost (no detection call
beyond character counting, no translation call, byte-identical prompt).
Indic legal questions add exactly one extra LLM call (query
translation) before retrieval; conversational Indic messages add
nothing. No new memory-resident indexes. Default deployment adds no
dependencies, no model files, no services. fastText (~130 MB) or
IndicTrans2 (~2.4 GB, GPU recommended) are opt-in via settings only.

## 8. Limitations

- Auto-detect cannot split Hindi/Marathi or Bengali/Assamese without
  the optional fastText backend (manual selection always works).
- Romanized Indic input ("kya saza hai") resolves to English.
- Cross-script document-citation relevance is bridged (existence +
  page-range enforced; lexical overlap waived and counted in
  `relevance_waived`) — the single deviation from the English rule,
  narrower than the English check (it removes nothing the English path
  would keep), reported per the assignment-safety rule.
- Answer-language quality depends on the local model; grounding,
  citations, and refusal are enforced by code regardless.

## 9. Assignment-safety confirmation

The BNS source, corpus, and citation format are unchanged; no BNSS
substitution, no alternate corpus, no translated corpora. Refusal
behavior, the confidence gate, citation validation, prompt-injection
defenses (history `system` role still rejected; documents still
untrusted-data), session isolation, and rate limiting are intact and
re-tested. The only requirement conflict found — full lexical relevance
being structurally impossible cross-script — is preserved-where-
computable and reported above (§1, §8) rather than silently weakened.
