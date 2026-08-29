# Nyaya — Implementation Plan

**Project:** Nyaya — Legal Assistant over the Bharatiya Nyaya Sanhita  
**Assignment:** DhronAI Technical Assignment  
**Status:** Pre-Implementation / Execution Plan  
**Implementation order:** Mandatory requirements first; bonus and polish only after the required baseline is verified.

---

## 1. Purpose

This document converts the project's existing specification into an ordered implementation sequence.

It is intentionally dependent on the other project documents rather than replacing them.

### Documents that govern implementation

| Document | Role |
|---|---|
| DhronAI Technical Assignment | Ultimate product/assignment authority |
| `docs/PRD.md` | Product scope, UX and behavior |
| `docs/REQUIREMENTS.md` | Atomic requirements and acceptance criteria |
| `docs/ARCHITECTURE.md` | Components, boundaries, data flows and lifecycles |
| `docs/DECISIONS.md` | Locked engineering choices |
| `docs/AI_RULES.md` | Rules for AI-assisted implementation |

`docs/REQUIREMENTS.md` is the master development checklist. A feature is not complete merely because code exists; it becomes `DONE` only after implementation and verification satisfy its acceptance criteria.

---

# 2. Execution Rules

Before every implementation task:

1. Read the relevant section of `docs/PRD.md`.
2. Identify the corresponding requirement IDs in `docs/REQUIREMENTS.md`.
3. Read the relevant architecture section in `docs/ARCHITECTURE.md`.
4. Follow the locked choice in `docs/DECISIONS.md`.
5. Follow the constraints in `docs/AI_RULES.md`.
6. Implement the smallest change that satisfies the requirement.
7. Write/run tests.
8. Verify actual behavior.
9. Update requirement status.
10. Update documentation if behavior or architecture changed.

### Never

- implement from this plan while ignoring the requirements matrix,
- invent unspecified requirements,
- silently change locked decisions,
- add unrelated product features,
- mark unverified work as `DONE`.

If a conflict exists, use the authority hierarchy in `docs/AI_RULES.md`.

---

# 3. Implementation Strategy

The implementation is divided into dependency-aware phases:

```text
Phase 0   Repository + Environment Baseline
   ↓
Phase 1   Backend Foundation
   ↓
Phase 2   Source Corpus + BNS Ingestion
   ↓
Phase 3   Dense + Sparse + Hybrid Retrieval
   ↓
Phase 4   Grounded Generation + Citation Guard
   ↓
Phase 5   User Documents + Isolation
   ↓
Phase 6   Forms Extraction
   ↓
Phase 7   Chat + Forms Frontend
   ↓
Phase 8   Observability + Evaluation
   ↓
Phase 9   Full Testing + Security
   ↓
Phase 10  Docker + CI/CD + Deployment
   ↓
Phase 11  Final Compliance Audit
   ↓
Optional: Bonus Reranking / Other Explicitly Allowed Enhancements
```

The phases are ordered so that later work depends on tested lower-level capabilities instead of creating a large unverified application first.

---

# 4. Phase 0 — Repository & Development Baseline

## Objective

Make the repository structurally correct and establish reproducible development conventions before feature implementation.

## Source documents

- `docs/PRD.md` — scope and documentation requirements
- `docs/REQUIREMENTS.md` — P-001 through P-010, SRC-008/SRC-009, repository/documentation requirements
- `docs/ARCHITECTURE.md` — runtime component boundaries
- `docs/DECISIONS.md` — locked technology choices
- `docs/AI_RULES.md` — Git, dependency, testing and clean-clone rules

## Tasks

### 0.1 Clean repository structure

Canonical specification documents must live under:

```text
docs/
├── PRD.md
├── REQUIREMENTS.md
├── ARCHITECTURE.md
├── DECISIONS.md
├── AI_RULES.md
└── IMPLEMENTATION_PLAN.md
```

Do not maintain duplicate authoritative copies at repository root.

### 0.2 Verify Git

- Rename the initial branch to `main`.
- Verify `.gitignore`.
- Ensure secrets, local databases, model caches, raw PDFs and generated local artifacts are excluded as appropriate.
- Make the first clean baseline commit only after the specification files are correct.

### 0.3 Backend project bootstrap

Create:

```text
backend/
├── app/
│   ├── api/
│   ├── core/
│   ├── forms/
│   ├── ingestion/
│   ├── llm/
│   ├── retrieval/
│   ├── workers/
│   └── main.py
└── tests/
```

### 0.4 Frontend bootstrap

Create the React + TypeScript + Vite application according to `DECISIONS.md`.

### 0.5 Configuration

Create `.env.example`.

No secrets are committed.

### 0.6 Tooling

Establish:

- formatter,
- linter,
- type checking,
- test runner,
- dependency locking/pinning,
- backend and frontend development commands.

## Exit criteria

- Repository starts cleanly.
- Backend imports successfully.
- Frontend builds successfully.
- Tests can execute.
- Lint/format/type-check commands execute.
- No secrets are present.
- Repository structure matches the architecture.

---

# 5. Phase 1 — Backend Foundation

## Objective

Build the FastAPI application shell and common infrastructure before implementing legal intelligence.

## References

- PRD §6, §7, §13
- Architecture §4.2, §38–§40
- Requirements Part D
- Decisions D-006 and related backend decisions

## Tasks

### 1.1 FastAPI application

Implement:

```text
backend/app/main.py
```

with modular routers/services.

Do not place all application logic in `main.py`.

### 1.2 API versioning

Use:

```text
/api/v1/
```

for required application endpoints.

### 1.3 Configuration layer

Implement typed configuration for:

- environment,
- database,
- Qdrant,
- Redis,
- LLM provider,
- embedding model,
- upload limits,
- rate limits,
- retrieval configuration,
- logging.

### 1.4 Database foundation

Implement the application metadata layer for the entities required by the PRD/architecture, including:

- sessions,
- conversations,
- messages,
- documents,
- jobs,
- feedback.

The exact schema must follow `docs/ARCHITECTURE.md` and `docs/DECISIONS.md`.

### 1.5 Session identity

Implement the selected anonymous/session identity approach from `DECISIONS.md`.

Requirements:

- no unnecessary account system,
- stable session identity for document ownership,
- ownership enforced server-side.

### 1.6 Request IDs

Every API request receives/propagates a request ID.

Include it in structured logs.

### 1.7 Health

Implement:

```text
GET /api/v1/health
GET /api/v1/health/ready
```

Distinguish liveness from dependency readiness.

### 1.8 Metrics endpoint

Implement:

```text
GET /api/v1/metrics
```

with Prometheus-compatible metrics.

## Tests

- application startup,
- configuration validation,
- session creation/identity,
- request ID behavior,
- health,
- readiness,
- metrics,
- API error format.

## Exit criteria

Backend shell is running, testable, observable and ready for domain services.

---

# 6. Phase 2 — Exact BNS Source + Structure-Aware Ingestion

## Objective

Create the authoritative BNS retrieval corpus correctly before building answer generation.

This is the highest-risk data-integrity phase.

## References

- PRD §4 and §8
- Architecture §6–§9
- Decisions D-001 through D-003, D-011/D-012
- Requirements SRC-* and A1-*

## Tasks

### 2.1 Acquire exact source

Use the exact BNS bare-act PDF specified/supplied by DhronAI.

Store it under:

```text
data/raw/
```

Do not substitute a differently paginated copy.

### 2.2 Record source identity

Record sufficient source information for reproducibility.

Do not commit the raw PDF.

### 2.3 Inspect the PDF

Before writing assumptions into the parser:

- inspect page count,
- inspect text layer,
- inspect layout,
- identify headers/footers,
- identify section patterns,
- identify chapter patterns,
- identify subsections/clauses,
- identify provisos/exceptions/explanations/illustrations,
- identify cross-reference patterns.

### 2.4 PDF cleanup

Remove/normalize extraction artifacts without altering legal meaning:

- running headers,
- running footers,
- page-number contamination,
- hyphenated line breaks,
- column ordering,
- marginal-note contamination where applicable.

Preserve source page numbers separately.

### 2.5 Structure parser

Build a parser that identifies:

```text
Act
 └── Chapter
      └── Section
           ├── Subsection
           ├── Clause
           ├── Proviso
           ├── Exception
           ├── Explanation
           └── Illustration
```

### 2.6 Section association

Correctly pair:

```text
section_number
section_title
section_text
```

Never infer section identity only from position.

### 2.7 Structure-aware chunker

Rules:

- short section → one chunk,
- long section → split only at subsection/clause boundaries,
- never split mid-sentence merely to hit a size,
- provisos remain attached,
- exceptions remain attached,
- explanations remain attached,
- illustrations remain attached,
- no orphaned legal components.

### 2.8 Overlap

Implement the overlap strategy locked in `DECISIONS.md`.

If the selected strategy requires tuning, measure its effect rather than changing it arbitrarily.

### 2.9 Metadata

Every chunk must preserve the required metadata from `REQUIREMENTS.md`, including:

```text
act
act_short
chapter
chapter_title
section_number
section_title
subsection
clause
text
has_illustration
has_proviso
has_exception
page_start
page_end
chunk_id
source_uri
ingested_at
references[]
```

### 2.10 Cross-references

Detect references such as:

```text
section 2(11)
section 103
```

and store them in `references[]`.

### 2.11 Chunk fixtures

Create representative fixtures for:

- short section,
- long section,
- subsection,
- clause,
- proviso,
- exception,
- explanation,
- illustration,
- cross-reference,
- page boundary,
- PDF cleanup cases.

### 2.12 Source validation

Before any source is treated as the authoritative corpus:

- validate the source **by content** (detected act title, structural
  invariants such as section count/ordering) against the expected corpus,
- never rely on the filename as evidence of corpus identity,
- reject a source that does not match the expected corpus — do not ingest
  it under a wrong label.

This requirement exists because the supplied PDF was found to contain BNSS
rather than the required BNS (see DECISIONS.md); development continues
against a temporary development corpus without changing the assignment
requirement.

### 2.13 Corpus identity and version metadata

Record corpus identity and source identity in ingestion/index metadata:

- corpus identity (act name, act short code) on every chunk,
- source identity (SHA-256, page count, detected act title, `ingested_at`)
  in a source manifest,

so every retrieval result and citation is traceable to an exact source
document version.

### 2.14 Replaceable, re-ingestible corpus

The source is replaceable without application-code changes:

- the corpus is defined by a configuration-level corpus spec, not hardcoded
  PDF assumptions,
- replacing the source PDF requires only re-running ingestion and
  re-indexing (deterministic, reproducible),
- temporary development corpora (e.g. the BNSS fixture) must never be
  relabeled or reinterpreted as BNS.

## Tests

Unit tests must prove structural behavior rather than merely non-empty output.

## Exit criteria

The active corpus is parsed deterministically after passing content-based
source validation, corpus and source identity are recorded in metadata,
legal structure is preserved, the ingestion output is ready for indexing,
and replacing the source requires re-ingestion only — no application-code
changes.

---

# 7. Phase 3 — Embeddings + Dense/Sparse/Hybrid Retrieval

## Objective

Build the required retrieval system independently from generation.

Retrieval operates against the **active validated corpus** (Phase 2 tasks
2.12–2.14): dense retrieval, sparse retrieval, and deterministic section
lookup all read the corpus the ingestion pipeline indexed, and corpus
identity comes from chunk/index metadata — never from a hardcoded
BNS/BNSS assumption in retrieval code. Swapping the corpus source means
re-running ingestion and re-indexing, not changing retrieval code.

## References

- PRD §9–§10
- Architecture §10–§16
- Decisions D-010 through D-016
- Requirements A2-* and A3-*

## Tasks

### 3.1 Embedding service

Implement the selected open-weight embedding model:

```text
BAAI/bge-base-en-v1.5
```

according to `DECISIONS.md`.

Record/verify:

- dimensions,
- sequence length,
- query/passage behavior,
- normalization,
- runtime configuration.

Do not invent model-specific behavior.

### 3.2 Batch embeddings

Implement batched embedding.

The model should load once per worker/process where practical.

Record throughput.

### 3.3 Qdrant

Set up Qdrant with persistent storage.

Logical separation:

```text
bns_chunks
user_document_chunks
```

or the equivalent architecture defined in `ARCHITECTURE.md`.

### 3.4 BNS dense indexing

Index:

```text
chunk embedding
+
required metadata
```

### 3.5 Sparse retrieval

Implement BM25/sparse retrieval according to D-013.

It must support exact legal identifiers.

### 3.6 Hybrid fusion

Implement:

```text
Dense top-k
+
Sparse top-k
↓
RRF
↓
Unified ranking
```

### 3.7 Metadata filters

Support:

```text
act
chapter
specific section
```

with server-side filtering.

### 3.8 Direct section lookup

Detect section-number intent.

Example:

```text
What is section 103 BNS?
```

must use deterministic lookup rather than relying solely on semantic similarity.

### 3.9 Retrieval routing

Implement:

```text
Statute → BNS retrieval
Document → session-document retrieval
Combined → both
```

At this phase, the document route may be implemented as an interface/stub only if the full document ingestion arrives in Phase 5; do not pretend it is functional.

### 3.10 Retrieval confidence

Implement the retrieval evidence/confidence mechanism described in the architecture.

Do not hardcode an arbitrary final threshold.

Initial configuration can be measurable/tunable.

### 3.11 Retrieval API/service

Create a retrieval service independent of the HTTP layer.

It should accept a query and routing/filter information and return structured evidence.

## Tests

Create retrieval fixtures for:

- exact section query,
- natural-language legal query,
- exact identifier query,
- dense-only candidate,
- sparse-only candidate,
- overlap between both,
- metadata filtering,
- no results,
- low-confidence results,
- RRF ordering.

## Exit criteria

Hybrid retrieval returns measurable, structured evidence with deterministic
section lookup and correct metadata, operating against the active validated
corpus without hardcoded corpus assumptions.

---

# 8. Phase 4 — Grounded LLM Generation + Citation Guard

## Objective

Turn retrieval evidence into safe, cited, streamed legal answers.

## References

- PRD §6
- Architecture §17–§19, §32–§35, §37
- Decisions LLM/citation/refusal decisions
- Requirements A4-* and chat/backend requirements

## Tasks

### 4.1 LLM provider abstraction

Implement:

```text
Application
    ↓
LLM interface
    ↓
configured provider
```

Support the selected hosted/local provider configuration in `DECISIONS.md`.

Ollama remains available for the keyless evaluation path where specified.

### 4.2 Grounded prompt contract

The generation layer receives retrieved evidence.

Rules:

- answer from supplied evidence,
- do not treat model memory as legal authority,
- distinguish BNS authority from user-document evidence,
- follow citation contract,
- refuse when evidence is insufficient.

### 4.3 Refusal

Implement the low-evidence refusal path.

Do not allow:

```text
retrieval failure → unsupported LLM answer
```

### 4.4 Citation generation

Require:

```text
[BNS s.103(1)]
```

or the appropriate citation form.

### 4.5 Citation validation

Implement executable post-generation validation:

```text
Generated answer
 ↓
Extract citations
 ↓
Compare citations with retrieved evidence
 ↓
Valid?
 ├─ yes → response
 └─ no → strip/regenerate according to policy
```

### 4.6 Source evidence

The response model must carry enough source information for the UI to show:

- exact retrieved text,
- source page,
- citation identity.

### 4.7 Streaming

Implement SSE or WebSocket according to the selected architecture.

The frontend must receive incremental output.

## Tests

- grounded answer,
- citation present,
- subsection citation,
- invalid citation,
- unsupported question,
- empty retrieval,
- low-confidence refusal,
- streaming event sequence,
- source evidence integrity.

## Exit criteria

A backend chat request can produce a grounded, cited, validated response from BNS evidence and refuses unsupported questions.

---

# 9. Phase 5 — User Document Ingestion + Isolation

## Objective

Implement secure upload, asynchronous processing, document retrieval and combined BNS/document questions.

## References

- PRD §7
- Architecture §20–§23 and §34–§36
- Decisions for session/queue/storage
- Requirements A5-* and Part D

## Tasks

### 5.1 Upload endpoint

Implement the required document upload API.

Validate:

- file type,
- file size,
- request/session ownership,
- malformed input.

### 5.2 Document metadata

Create application records for:

- document ID,
- session owner,
- filename,
- status,
- processing job,
- timestamps,
- failure information.

### 5.3 Async queue

Use the locked Redis/task-queue decision.

Lifecycle:

```text
QUEUED
 ↓
PARSING
 ↓
CHUNKING
 ↓
EMBEDDING
 ↓
INDEXING
 ↓
READY
```

Failure:

```text
FAILED
 + error_code
 + error_message
```

### 5.4 PDF parsing

Reuse appropriate parsing infrastructure from Phase 2 where possible.

### 5.5 Document chunking

Document chunking may differ from statutory chunking because user documents are not the BNS legal corpus.

Do not accidentally apply a document strategy to BNS.

### 5.6 Document embeddings

Embed/index user-document chunks.

### 5.7 Session-scoped retrieval

Every document retrieval must include the session/owner scope.

Never retrieve globally and filter afterward.

### 5.8 Unauthorized access

Attempting to access another session's document must return:

```text
404
```

### 5.9 Delete

Deletion must purge:

- metadata,
- file,
- vector records.

### 5.10 Prompt-injection boundary

Treat uploaded text as untrusted evidence.

Document instructions must never override system/application rules.

### 5.11 Query routing

Implement:

```text
BNS only
Document only
BNS + Document
```

and distinguish the evidence in the final answer.

### 5.12 Status API

Implement the required status/list/delete APIs.

## Tests

- valid upload,
- unsupported file,
- oversized file,
- malformed PDF,
- async lifecycle,
- processing failure,
- session isolation,
- cross-session access,
- deletion,
- vector purge,
- prompt injection,
- combined query.

## Exit criteria

A user can upload a document, observe processing, query it after `READY`, combine it with BNS evidence, and cannot access another session's document.

---

# 10. Phase 6 — Forms Extraction Pipeline

## Objective

Extract the required statutory forms from the specified source range programmatically and reproducibly.

## References

- PRD §4.2 and §12
- Architecture §24–§31
- Decisions D-002 and forms-related decisions
- Requirements Part B

## Tasks

### 6.1 Source range

Initial processing target:

```text
Pages 190–249
```

Use actual page content as authoritative.

If the observed content differs from the expected range, document it in `DECISIONS.md`.

### 6.2 Page inspection

Inspect page structure before implementing detection assumptions.

### 6.3 Form title detection

Detect titles programmatically.

Do not hardcode a list of form titles.

### 6.4 Form boundary detection

Determine:

- form start,
- form continuation,
- form end.

### 6.5 Multi-page forms

Pages belonging to one form must be emitted as one PDF.

Never assume:

```text
one page = one form
```

### 6.6 PDF generation

Generate page-perfect form PDFs.

### 6.7 Naming

Use:

```text
FORM-<number>_<slugified-title>.pdf
```

### 6.8 Manifest

Generate the required manifest with:

```text
form_number
title
source_page_start
source_page_end
output_filename
byte_size
sha256
extraction_confidence
needs_review
```

### 6.9 OCR fallback

Use normal text extraction first.

Use OCR only where the source text layer is absent/unusable.

Log OCR usage.

### 6.10 Idempotency

Running extraction twice on identical input must produce equivalent outputs and must not duplicate forms.

## Tests

- title extraction,
- form numbering,
- single-page form,
- multi-page form,
- page boundaries,
- OCR fallback,
- filename safety,
- manifest correctness,
- SHA-256,
- repeat execution.

## Exit criteria

The forms library is generated from the source programmatically, has correct boundaries/metadata, and is reproducible.

---

# 11. Phase 7 — Frontend: Chat + Forms

## Objective

Build the required two-panel product UI after the backend contracts are stable.

## References

- PRD §5–§6
- PRD §12
- Architecture §4.1 and §37
- Decisions D-007 through D-009
- Requirements Part C

## Tasks

### 7.1 Application shell

Create the two primary panels:

```text
Chatbot
Forms
```

### 7.2 Chat

Implement:

- conversation list,
- new conversation,
- rename,
- delete,
- multi-turn messages,
- Markdown,
- code blocks,
- quote blocks,
- copy,
- stop,
- regenerate,
- streaming response.

### 7.3 Citation chips

Render citations as clickable UI elements.

### 7.4 Source drawer

Clicking a citation opens:

- exact retrieved text,
- source page,
- relevant source information.

### 7.5 Disclaimer

Persistent panel chrome:

```text
Not legal advice
```

Do not repeat it in every message.

### 7.6 Upload UI

Implement:

```text
upload
 ↓
parse
 ↓
chunk
 ↓
embed
 ↓
ready
```

with visible status/progress.

### 7.7 Forms panel

Implement:

- search,
- filtering,
- preview,
- single download,
- bulk ZIP download.

### 7.8 Errors

Show useful errors for:

- upload failure,
- processing failure,
- query failure,
- unavailable service,
- unsupported input.

Do not expose internal stack traces.

### 7.9 Responsive/accessibility

Implement:

- mobile usability,
- keyboard navigation,
- focus states,
- appropriate ARIA,
- contrast,
- light mode,
- dark mode.

## Tests

- frontend build,
- chat rendering,
- streaming,
- citation interaction,
- source drawer,
- conversation operations,
- upload status,
- forms search,
- download,
- responsive layouts,
- keyboard accessibility.

## Exit criteria

A reviewer can use the complete core product without manually interacting with backend internals.

---

# 12. Phase 8 — Evaluation + Observability

## Objective

Make retrieval quality, refusal behavior, latency and runtime health measurable.

## References

- PRD §3.1 Evaluation/Observability
- Architecture §41–§42
- Requirements Part F
- Decisions retrieval/evaluation decisions

## Tasks

### 8.1 Golden set

Populate:

```text
eval/golden_set.jsonl
```

with:

```text
25–30 questions
```

including at least:

```text
5 out-of-scope questions
```

Include a meaningful mix of:

- direct section questions,
- semantic legal questions,
- exact identifiers,
- questions requiring citations,
- unsupported/out-of-scope questions.

### 8.2 Evaluation runner

Implement:

```text
eval/run_eval.py
```

and structured result output.

### 8.3 Retrieval metrics

Calculate:

```text
Recall@5
Recall@10
MRR
```

### 8.4 Answer metrics

Calculate/measure:

```text
citation accuracy
refusal rate
```

### 8.5 Latency

Measure:

```text
p50
p95
retrieval latency
generation latency
```

### 8.6 Configuration comparison

Compare at least two retrieval configurations.

Required baseline comparison should make the effect of hybrid retrieval measurable.

### 8.7 Confidence calibration

Use evaluation evidence to select/refine the refusal threshold.

Do not optimize only for answer rate.

### 8.8 Metrics

Expose Prometheus metrics for the required runtime measurements.

### 8.9 Dashboard/scraping

Provide the required Grafana dashboard or documented `/metrics` scraping with evidence according to the assignment.

### 8.10 Cost

Document estimated query cost for the selected generation configuration.

## Exit criteria

The system produces reproducible evaluation results and exposes enough telemetry to explain retrieval quality and runtime performance.

---

# 13. Phase 9 — Full Testing + Security Hardening

## Objective

Test the integrated system against normal and adversarial failure modes.

## References

- `docs/AI_RULES.md` testing/security rules
- PRD testing/security scope
- Requirements Part D, Part F and final audit
- Architecture security/isolation sections

## 9.1 Unit tests

Cover:

- parsing,
- chunking,
- metadata,
- citations,
- routing,
- validation,
- filenames,
- manifest,
- confidence logic.

## 9.2 Integration tests

Cover:

- BNS ingestion → indexing,
- retrieval → generation,
- upload → queue → worker → indexing,
- forms extraction,
- deletion → vector purge.

## 9.3 API tests

Cover every required endpoint.

## 9.4 End-to-end test

At minimum:

```text
upload document
    ↓
processing
    ↓
READY
    ↓
query document
    ↓
retrieve evidence
    ↓
generate answer
    ↓
validate citations
    ↓
stream cited answer
```

## 9.5 Security tests

Test:

- session isolation,
- cross-session document access,
- prompt injection,
- unsupported files,
- oversized files,
- malformed PDFs,
- rate limiting,
- secret scanning,
- unsafe error leakage.

## 9.6 Retrieval red-team

Test:

- exact section number,
- similar section numbers,
- semantically similar but incorrect sections,
- no-evidence queries,
- unsupported legal questions,
- document text attempting instruction override.

## 9.7 Forms red-team

Test:

- continuation pages,
- malformed title extraction,
- missing text layer,
- OCR,
- duplicate execution.

## Exit criteria

The integrated application passes the required test suite and known failure modes are documented.

---

# 14. Phase 10 — Docker, CI/CD & Deployment

## Objective

Make the application reproducible and deployable.

## References

- PRD infrastructure/CI/CD
- Architecture runtime/infrastructure sections
- Requirements Part 7, Part 9, Part 10, Part 15
- AI_RULES Docker/security rules

## 10.1 Docker services

Implement the architecture's service topology:

```text
Frontend
API
Worker
Qdrant
Redis/Queue
Application database
```

where required by the selected architecture.

### 10.2 Persistent volumes

Persist required state using named volumes.

### 10.3 Shared network

Services communicate through the Compose network.

### 10.4 Health checks

Add health checks for services where required.

### 10.5 Non-root

Use non-root containers where applicable.

### 10.6 Pinned dependencies

Build reproducibly with pinned dependency versions.

### 10.7 Clean-clone startup

Verify the documented startup path from a clean checkout.

### 10.8 One-shot ingestion

BNS ingestion must not run on every container startup.

Use the documented bootstrap/one-shot mechanism.

### 10.9 GitHub Actions

CI should execute:

```text
lint
format check
type check
tests
coverage
secret scan
Docker build
```

### 10.10 GHCR

Build/publish the required container images with immutable SHA-based tags according to the assignment.

### 10.11 Trivy

Scan built images.

### 10.12 Deployment

Implement the selected deployment path.

If DevOps-track requirements apply, implement the self-hosted runner requirements specified by the assignment.

## Exit criteria

The application can be built, started and verified through the documented container/CI workflow.

---

# 15. Phase 11 — Final Compliance Audit

## Objective

Prove that the implementation satisfies the assignment rather than merely appearing complete.

## Primary source

`docs/REQUIREMENTS.md` §19–§20.

## Procedure

### 11.1 Requirement-by-requirement audit

For every requirement:

```text
ID
↓
Implementation location
↓
Test location
↓
Verification result
↓
Status
```

Use only:

```text
TODO
IN_PROGRESS
DONE
PARTIAL
BLOCKED
N/A
```

as defined in the requirements matrix.

### 11.2 Mandatory retrieval audit

Verify:

- exact source PDF,
- structure-aware chunking,
- metadata,
- embeddings,
- dense retrieval,
- sparse retrieval,
- hybrid fusion,
- filters,
- direct section lookup,
- citations,
- refusal.

### 11.3 Forms audit

Verify:

- source range,
- programmatic detection,
- multi-page grouping,
- page-perfect output,
- filenames,
- manifest,
- OCR fallback,
- idempotency.

### 11.4 Frontend audit

Verify:

- Chatbot panel,
- Forms panel,
- streaming,
- history,
- citations,
- source drawer,
- upload status,
- form search/filter,
- downloads,
- responsive/accessibility requirements,
- disclaimer.

### 11.5 Backend audit

Verify all required endpoints, validation, queue, identity, isolation, logging, health, readiness and metrics.

### 11.6 CI/CD audit

Verify all applicable CI/CD/security/deployment requirements.

### 11.7 Evaluation audit

Verify:

- golden set size,
- out-of-scope questions,
- retrieval metrics,
- citation accuracy,
- refusal rate,
- latency,
- configuration comparison.

### 11.8 Documentation audit

Verify:

- README,
- PRD,
- requirements,
- architecture,
- decisions,
- AI usage disclosure,
- setup instructions,
- API examples,
- evaluation results,
- known limitations.

---

# 16. Definition of Done for Each Phase

A phase is complete only when:

```text
All planned tasks implemented
        ↓
Relevant tests written
        ↓
Relevant tests pass
        ↓
Actual behavior inspected
        ↓
Requirements updated
        ↓
Documentation updated if necessary
        ↓
No known blocker hidden
        ↓
Git commit created
```

A phase must not be marked complete merely because the files/functions exist.

---

# 17. Definition of Done for the Whole Project

The project is ready for submission only when:

```text
Mandatory requirements
        +
Acceptance criteria
        +
Tests
        +
Evaluation
        +
Security
        +
Docker
        +
CI/CD
        +
Documentation
        +
Final compliance audit
```

are complete or honestly documented as `PARTIAL/BLOCKED`.

---

# 18. Git Commit Strategy

Use small, meaningful commits aligned with implementation units.

Examples:

```text
chore: bootstrap backend and frontend
feat: add fastapi application foundation
feat: add bns pdf structure parser
test: add legal chunking fixtures
feat: add bns qdrant indexing
feat: add hybrid bm25 retrieval
feat: add deterministic section lookup
feat: add citation validation
feat: add grounded streaming chat
feat: add async document ingestion
feat: add session document isolation
feat: add forms extraction pipeline
feat: add chat and forms frontend
test: add end-to-end document query flow
feat: add evaluation runner
chore: add docker and ci pipeline
docs: complete final compliance audit
```

Do not use vague commits such as:

```text
final
fix
changes
everything
```

---

# 19. AI Vibecoding Execution Protocol

When using an AI coding agent, do not give it the entire project as one unconstrained task.

Use phase-scoped prompts.

### Recommended pattern

```text
Read:
- docs/PRD.md
- docs/REQUIREMENTS.md
- docs/ARCHITECTURE.md
- docs/DECISIONS.md
- docs/AI_RULES.md
- docs/IMPLEMENTATION_PLAN.md

Implement:
<ONE phase/task>

Requirements:
<exact IDs>

Do not:
- change architecture,
- add scope,
- invent missing behavior.

After implementation:
1. run tests,
2. run lint/type checks,
3. inspect output,
4. report failures,
5. update requirement status.
```

### One task at a time

Prefer:

```text
"Implement A1-001 through A1-010."
```

over:

```text
"Build the entire Nyaya application."
```

This reduces uncontrolled code generation and makes failures traceable.

---

# 20. Recommended First Coding Sequence

After this document is committed, begin implementation in exactly this order:

```text
1. Repository cleanup
2. Backend + frontend bootstrap
3. Backend foundation
4. BNS source acquisition/verification
5. BNS parser
6. Structure-aware chunker
7. Chunk tests
8. Embedding service
9. Qdrant indexing
10. Sparse/BM25 retrieval
11. RRF hybrid retrieval
12. Direct section lookup
13. Retrieval evaluation
14. LLM provider abstraction
15. Citation validator
16. Streaming chat API
17. User-document upload
18. Async worker
19. Document retrieval/isolation
20. Combined retrieval
21. Forms extraction
22. Forms API
23. Frontend
24. Evaluation/observability
25. Full tests/security
26. Docker
27. CI/CD
28. Final audit
```

Do not move to a dependent step while the preceding critical step is unverified.

---

# 21. Dependency Gates

The following gates are mandatory:

### Gate A — Before Generation

```text
BNS parsing
+
chunking
+
indexing
+
retrieval
```

must work.

### Gate B — Before Frontend Completion

```text
Chat API
+
streaming
+
citations
+
source evidence
```

must work.

### Gate C — Before Combined Queries

```text
BNS retrieval
+
document retrieval
+
session isolation
```

must work independently.

### Gate D — Before Final Submission

```text
Tests
+
evaluation
+
Docker
+
CI/CD
+
documentation
+
compliance audit
```

must be verified.

---

# 22. What Must Wait Until the Baseline Works

Do not prioritize these before mandatory functionality is stable:

- cross-encoder reranking,
- additional legal corpora,
- extra product features,
- agentic workflows,
- voice,
- mobile-native applications,
- general web search,
- unrelated dashboards,
- speculative performance optimizations.

The assignment/PRD explicitly places such work outside the required baseline.

---

# 23. Change-Control During Implementation

If a requirement appears impossible or an architectural decision becomes invalid:

```text
STOP
 ↓
Identify exact requirement/decision
 ↓
Explain the conflict
 ↓
Assess impact
 ↓
Update docs/DECISIONS.md
 ↓
Update docs/ARCHITECTURE.md if necessary
 ↓
Update affected requirements/tests
 ↓
Continue implementation
```

Never silently work around a specification conflict.

---

# 24. Traceability Model

Every implemented feature should be traceable in both directions:

```text
Requirement
    ↓
Implementation
    ↓
Test
    ↓
Evidence
```

and:

```text
Code
    ↓
Requirement ID
    ↓
Assignment requirement
```

This is particularly important because the assignment requires every submitted line to be explainable by the candidate.

---

# 25. Final Principle

The implementation plan is not permission to improvise.

It is the execution order for the already-defined product.

The governing chain remains:

```text
DhronAI Assignment
        ↓
docs/PRD.md
        ↓
docs/REQUIREMENTS.md
        ↓
docs/ARCHITECTURE.md
        ↓
docs/DECISIONS.md
        ↓
docs/AI_RULES.md
        ↓
docs/IMPLEMENTATION_PLAN.md
        ↓
Code
        ↓
Tests
        ↓
Verification
```

**Build only what is required, build it in dependency order, verify every meaningful step, and document every legitimate deviation.**