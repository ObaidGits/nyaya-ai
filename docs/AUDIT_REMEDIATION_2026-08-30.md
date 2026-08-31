# NYAYA AI-Assistant Audit Remediation — 2026-08-30

Authoritative source: *NYAYA AI-Assistant Bug Hunt — 2026-08-30* (18 findings).
Every finding is either FIXED (with code + regression-test evidence) or
explicitly NOT APPLICABLE (with proof). No grounding, citation, refusal,
security, or source-integrity requirement was weakened; no test was changed
only to make it pass — all pre-existing assertions were preserved and new
assertions added.

Test commands (run on host; sandbox has no working venv interpreter):

```
cd backend
.venv/bin/python -m pytest -q
.venv/bin/ruff check app tests
.venv/bin/ruff format --check app tests
.venv/bin/mypy
```

Live E2E requires the rebuilt image (backend code is baked in):

```
docker compose up -d --build api
curl -N -X POST localhost:8000/api/v1/chat \
  -H 'Content-Type: application/json' -d '{"message":"Who are you"}'
```

---

## Finding-by-finding disposition

### 1. [HIGH] Irrelevant statutory citations on non-legal questions — FIXED

Two independent fixes, either of which stops the symptom:

- **Routing fix (D-068):** `app/generation/conversation.py` now
  recognizes identity ("who are you"), capability ("what can you do",
  "can you help me"), well-being ("how are you"), and acknowledgement
  ("ok", "sure") classes via anchored whole-message regexes. These
  messages never reach retrieval, so no evidence block can enter the
  prompt and no citation can be demanded.
- **Guard fix (D-072):** even if a citation-bearing sentence reaches the
  guard, the relevance layer strips citations from self-referential
  sentences ("I am Nyaya … [BNSS s.465]") and removes sentences with zero
  content-token overlap with the cited chunk.

Regression tests: `tests/generation/test_conversation.py`
(`test_identity_question_gets_fixed_reply_without_llm`,
`test_capability_question_gets_fixed_reply_without_llm`, plus unit
matrices for identity/capability/how-are-you/ack classes and
non-conversational fall-through including "who are you and what is
section 103"); `tests/generation/test_citation_guard.py`
(`test_self_referential_sentence_loses_citation_but_keeps_text`,
`test_irrelevant_citation_zero_overlap_removes_sentence`);
`tests/generation/test_service.py`
(`test_irrelevant_citation_creates_no_source` — no source-drawer entry
either).

### 2. [HIGH] Citation guard checks existence, never relevance — FIXED

`validate_citations` is now a three-layer executable check
(D-072, `app/generation/citation_guard.py`): existence → subsection
granularity → lexical relevance (≥ 1 shared content token between the
claim sentence and the cited chunk, after stopword removal). Citations
attached to self-referential or content-free sentences are stripped;
zero-overlap sentences are removed entirely. Sources are minted only
from surviving citations.

Regression tests: `test_citation_guard.py` (relevance, self-referential,
content-free, zero-overlap cases), `test_service.py`
(`test_irrelevant_citation_creates_no_source`).

### 3. [HIGH] Document-route retrieval has no confidence gate — FIXED

`DocumentEvidence.sufficient` now requires the top document hit's cosine
score to meet `document_retrieval_confidence_threshold` (default 0.05,
calibrated to the HashingEmbedder cosine scale, where a genuinely
matching notice chunk scores ~0.08 — a 0.1 default false-refused real
document answers) (D-073). The threshold is configurable via settings and
wired through app construction (`main.py`) and the chat fallback path
(`api/v1/chat.py:get_retrieval_service`). Below threshold: refusal with
reason "document retrieval confidence below threshold", no LLM call.

Regression tests: `tests/retrieval/test_service.py`
(`test_document_route_sufficient_when_confidence_meets_threshold`,
`test_document_route_refuses_when_confidence_below_threshold`,
`test_document_gate_default_calibrated_to_hashing_cosine_scale`).

### 4. [MEDIUM] Chat history accepts `role: "system"` — FIXED

`ChatTurn.role` now validates user/assistant only via a Pydantic
`field_validator` (D-069); a system (or unknown) role in history returns
422 before the pipeline runs. The server still constructs internal system
messages — only client input is restricted.

Regression test: `tests/generation/test_conversation.py`
(`test_system_role_in_history_is_rejected`); pre-existing
`test_hardening.py::test_chat_rejects_unknown_history_role` still passes.

### 5. [MEDIUM] Prose section claims without Act mention escape the guard — FIXED

The prose gate (`_prose_gate`) no longer requires an Act label: a
"section N …" claim is supported when the section number exists in the
retrieved evidence regardless of Act mention; unsupported claims are
removed with their sentence and recorded in
`check.uncited_section_claims`.

Regression tests: `test_citation_guard.py`
(`test_uncited_prose_section_claim_is_removed`,
`test_supported_prose_section_claim_is_kept`).

### 6. [MEDIUM] Subsection never validated — FIXED

Granularity layer (D-072): a `[Act s.N(k)]` citation requires a chunk
with that exact `subsection`, or a whole-section chunk whose verbatim
text contains the subsection marker. Failures are recorded as
`subsection_mismatches` and the sentence is removed.

Regression test: `test_citation_guard.py`
(`test_subsection_citation_requires_subsection_evidence`).

### 7. [MEDIUM] Bare section numbers skip deterministic lookup — FIXED

`detect_section_intent` recognizes bare 1-3 digit numbers ("What does
103 say?", "Explain 103(1)") with three exclusion guards: unit followers
("30 days", "7 years", "500 rupees", "2 lakh"), non-statute preceders
(case/no/page/pg/form/fir/chapter/article/…), and decimal/IP-style
continuations ("7.5 lakh", "169.254.169.254") (D-075). Ambiguous input
falls through to hybrid retrieval — the failure mode is the old
behavior, never a wrong deterministic answer.

Regression tests: `test_intent.py`
(`test_bare_number_section_intent`,
`test_bare_number_with_subsection_intent`,
`test_quantities_and_identifiers_are_not_section_intents` — 14
parametrized negative cases); `test_conversation.py`
(`test_bare_section_number_takes_deterministic_rag_path`).
Golden-set check: every golden lookup question uses the
"section N" phrasing (no bare numbers), so
`test_golden_lookup_questions_resolve_via_direct_lookup` is unaffected.

### 8. [MEDIUM] Empty model response → blank message — FIXED

`GenerationService` retries once on an empty provider response
(`generation_empty_response` log), then raises
`EmptyGenerationError` → 503 `LLM_EMPTY_RESPONSE` error event. An empty
completion is a provider failure, never a blank 200 answer.

Regression tests: `test_service.py`
(`test_empty_provider_responses_raise_app_error`),
`test_conversation.py`
(`test_empty_provider_responses_stream_safe_error`).

### 9. [MEDIUM] Model-emitted refusal indistinguishable — FIXED

Post-generation normalization: if the model outputs the refusal text,
the outcome becomes `refused=True`, `REFUSALS.inc()` fires, and the
`done` meta reports the truthful state (`generation_refusal` log with
reason `model_emitted`) (D-074).

Regression tests: `test_service.py`
(`test_model_emitted_refusal_text_is_normalized`),
`test_conversation.py` (`test_model_emitted_refusal_is_normalized`).

### 10. [MEDIUM] "Good evening"/"hi" with citations — NOT APPLICABLE (unreproducible on current code)

Exact-match strings "good evening" and "hi" short-circuit before
retrieval and emit `sources: []` and `citations: []` by construction
(D-067). The observed capture required either a stale image or a
phrasing variant falling into finding 1 — the variant case is now
covered by the finding-1 fix. Verification step for the rebuilt image
included in the host E2E list below.

### 11. [MEDIUM] Generation blocking; streaming cosmetic — NOT APPLICABLE (deliberate design)

Validate-before-stream is the security property that no invalid
citation can ever be streamed (audit §8 "currently working correctly"
lists it). Assignment #11 is satisfied at the protocol level
(progressive SSE token events). True token-streaming would require
validating partial text — impossible without weakening the guard.
Documented here as an accepted trade-off, not a defect.

### 12. [MEDIUM] "Brain active" = transport reachability only — FIXED

`ModelProviderCheck` for Ollama now fetches `/api/tags`, parses the
model list, and verifies the configured model (or the
`llama3.1:8b` default when unset) is present, matching exact name or
`model:`-tag prefix. Reachable-server-without-model is a FAIL; so are
unreachable, HTTP ≥ 400, and invalid JSON (D-074).

Regression tests: `tests/test_health.py` (6 tests: present, prefix
variant, missing → FAIL, unreachable → FAIL, model-less transport-only
OK, invalid JSON → FAIL).

### 13. [MEDIUM] Act-mismatch fallback feeds wrong-act evidence — FIXED

The act-restricted-lookup retry without the act filter now runs only
when the corpus holds a single Act (alias case). In a multi-Act corpus
the miss is explicit: refusal reason
"act X not present in the indexed corpus" — no silent wrong-act
substitution (D-071).

Regression tests: `test_service.py`
(`test_act_alias_fallback_in_single_act_corpus`,
`test_act_mismatch_refuses_in_multi_act_corpus`).

### 14. [MEDIUM] Dense retrieval is lexical hashing — NOT APPLICABLE (documented seam)

`HashingEmbedder` is the documented D-011/D-012 seam: swapping in a
semantic embedder is a deployment change behind a stable interface, out
of scope for this remediation (no model download permitted, corpus
replaceable architecture preserved). The relevance-layer fix (finding 2)
addresses the downstream consequence the audit actually demonstrated:
hashing-induced junk evidence can no longer produce a rendered citation
on a non-legal claim.

### 15. [LOW] Stop-generation persists answer without sources — NOT APPLICABLE (accepted consequence)

Direct consequence of validate-before-stream (finding 11): the
`sources` event follows the full validated answer, so an aborted stream
keeps partial text without drawer payload. Regenerate is the recovery
path. Documented; frontend untouched (assignment scope lock).

### 16. [LOW] Observability blind spots on citation path — FIXED

`generation_complete` now logs `answer_length`, `citations_valid`,
`citations_invalid`, `citations_irrelevant`, `documents_cited`, and
`model` (no answer text). `retrieval_complete` logs the section intent.
Refusal events log their reason (`model_emitted`,
`empty_answer_after_validation`, gate reasons) (D-074).

Regression coverage: metrics/refusal counter tests in
`tests/evaluation/test_metrics_api.py` and the refusal-normalization
tests above.

### 17. [LOW] Whitelist coverage narrow — FIXED

Covered by the D-068 expansion (finding 1): identity, capability,
well-being, acknowledgements, "good night", "can you help me". Anything
with additional substantive content still falls through to RAG —
conservative by design (false fall-through costs one turn; false
interception would be a grounding violation).

### 18. [LOW] Residual prompt-injection via valid-section citations — FIXED (layer mitigates)

The relevance layer closes the specific residual the audit named:
injected text steering the model to cite an evidence-present section now
fails unless the sentence shares content with that chunk —
`tests/security/test_hardening.py::test_injected_fake_citation_is_stripped_by_guard`
exercises exactly this (attempt 2 cites the real BNSS s.234 with zero
lexical overlap → sentence removed → empty answer → refusal). Residual
risk of a 3B-model identity override is acknowledged as a model-capability
limitation, not a code defect; the layered guard remains the boundary.

---

## Coverage added for the audit's "missing test coverage" list

Identity/capability questions; casual-with-history; legal→casual and
casual→legal transitions (test_conversation.py); citation-on-non-legal-
claim rejection; citation relevance (not just existence); subsection
mismatch; document-route insufficient-confidence refusal; history
system-role rejection; empty provider response; model-written refusal
detection; bare "103" lookup determinism; observability fields
(generation_complete attributes asserted indirectly via service tests;
metrics tests assert counters).

## Remaining limitations (documented, out of remediation scope)

Blocking generation with post-validation token streaming (by design,
finding 11); HashingEmbedder density (D-011/D-012 seam, finding 14);
stop-button partial answers (finding 15); BNSS-only dev corpus (assignment
constraint); 3B-model instruction-following quality (guard enforces the
contract regardless).

## Post-verification fix (2026-08-30, host test run)

The host run surfaced one latent defect the unit suite had not pinned:
document chunk ids encoded the 0-based `PageText.index` while the chunk
metadata (and document citations) use 1-based pages, so
`_parse_chunk_id` rebuilt first-page hits as page 0 and every
`[Document X p.1]` citation failed the guard's page-range check —
document chat refused despite correct retrieval and generation. The
document confidence gate default was also recalibrated to 0.05 (D-073,
HashingEmbedder cosine scale; real matches score ~0.08). Fixed in
`app/documents/chunker.py` (D-076) with regression tests
(`test_chunk_id_page_encoding_matches_page_metadata`,
`test_chat_answers_document_questions_from_session_documents`); the
document-threshold calibration is pinned by
`test_document_gate_default_calibrated_to_hashing_cosine_scale`.
