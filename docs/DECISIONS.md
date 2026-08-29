# Nyaya — Engineering Decisions

**Project:** Nyaya — Legal Assistant over the Bharatiya Nyaya Sanhita  
**Assignment:** DhronAI Technical Assignment  
**Document Status:** Pre-Implementation / Architecture Lock  
**Authority:** DhronAI assignment first; `docs/PRD.md` and `docs/REQUIREMENTS.md` translate it into implementation scope.

---

## 1. Purpose

This document locks the engineering choices that the assignment leaves open.

The coding agent must **not make these decisions again during implementation**.

If a decision must change:

1. Update this document first.
2. Explain why the original decision is insufficient.
3. Update `ARCHITECTURE.md` if the architecture changes.
4. Update affected requirements/tests.
5. Then implement the change.

The assignment explicitly asks for trade-offs and decisions around embedding model, chunking, overlap, hybrid retrieval, reranking, confidence threshold, session model, queue, and known weaknesses. The choices below are therefore deliberate engineering decisions, not claims that the assignment mandates these exact technologies. fileciteturn4file0L42-L52

---

# 2. Decision Classification

We use four categories:

| Category | Meaning |
|---|---|
| `MANDATED` | Explicitly required by the assignment |
| `PREFERRED` | Assignment explicitly prefers/recommends it |
| `DECIDED` | Our engineering choice among permitted options |
| `BONUS` | Explicitly optional/heavily rewarded but not mandatory |

---

# 3. Non-Negotiable Assignment Constraints

These are **not changeable engineering decisions**.

## D-001 — Exact Source PDF

**Classification:** `MANDATED`

Use the exact BNS PDF supplied by DhronAI.

Do not substitute a differently paginated copy.

The assignment states that all candidates are working from the exact file and that page references/forms manifest are built against it. fileciteturn4file2L134-L143

### Decision

```text
data/raw/<exact-source-pdf>
```

The source PDF is not committed to Git.

---

## D-002 — Forms Range

**Classification:** `MANDATED`

Initial forms processing target:

```text
Pages 190–249
```

However, the assignment explicitly warns that the linked PDF is a combined/annotated volume and the parser must inspect what is actually on each page rather than assuming statute identity. If our observed range/content differs, that discrepancy must be documented in this file. fileciteturn4file0L11-L16

### Decision

The forms extractor is **content-driven**, with pages 190–249 as the assignment-defined processing range.

---

## D-003 — Structure-Aware Legal Chunking

**Classification:** `MANDATED`

We will not use:

```text
RecursiveCharacterTextSplitter(chunk_size=512)
```

as the statutory chunking strategy.

The assignment explicitly says this is an automatic fail for Part A. fileciteturn4file0L17-L21

### Decision

The chunker is section-aware and subsection/clause-aware.

---

## D-004 — Hybrid Retrieval

**Classification:** `MANDATED`

Dense-only retrieval is prohibited.

The assignment requires dense + BM25/sparse/full-text retrieval with result fusion. fileciteturn4file8L419-L426

### Decision

Nyaya will use:

```text
Dense retrieval
+
Sparse/BM25 retrieval
+
Reciprocal Rank Fusion (RRF)
```

---

## D-005 — Citation Contract

**Classification:** `MANDATED`

Every legal statement must carry:

```text
Act + Section
+ subsection when relevant
```

The assignment gives:

```text
[BNS s.103(1)]
```

as the expected style and requires a source panel showing retrieved text verbatim and page number. fileciteturn4file8L427-L436

### Decision

Citation validation will be implemented as executable application code, not prompt instructions alone.

---

# 4. Backend Framework

## D-006 — FastAPI

**Classification:** `DECIDED`

### Choice

```text
FastAPI + Python
```

### Why

- Assignment permits FastAPI/Python.
- Native async support.
- Strong typing through Pydantic.
- Excellent OpenAPI support.
- Good fit for streaming APIs.
- Good fit for Python PDF/embedding/ML ecosystem.
- Keeps ingestion and retrieval in the same language/runtime.

### Rejected Alternative

```text
NestJS / Express / Node
```

Not because it is unsuitable, but because Python gives the retrieval/PDF/embedding pipeline a more direct implementation path.

---

# 5. Frontend Framework

## D-007 — React + Vite

**Classification:** `DECIDED`

### Choice

```text
React + TypeScript + Vite
```

### Why

The assignment requires React and permits either plain React or Next.js.

Vite is selected because Nyaya is primarily an authenticated/session-based application UI rather than an SEO/content-heavy website.

### Consequences

- Client-side application shell.
- Fast local development.
- Simple Docker build.
- Static frontend artifact can be served efficiently.
- API remains fully independent.

### Rejected Alternative

```text
Next.js
```

Next.js is valid but its server-side features are not required for the assignment's core workflows.

---

# 6. Frontend Language

## D-008 — TypeScript

**Classification:** `DECIDED`

### Choice

```text
TypeScript
```

### Why

- Prevents API shape drift.
- Strong typing for streamed chat events.
- Safer citation/source models.
- Better maintainability for a second engineer.

---

# 7. Frontend Styling

## D-009 — Tailwind CSS

**Classification:** `PREFERRED → DECIDED`

The assignment says Tailwind is the default expectation unless there is a good reason otherwise.

### Choice

```text
Tailwind CSS
```

### Rule

Do not introduce a second large styling framework.

Custom CSS is allowed where needed for specific UI behavior.

---

# 8. Vector Database

## D-010 — Qdrant

**Classification:** `PREFERRED → DECIDED`

### Choice

```text
Qdrant
```

The assignment explicitly permits Qdrant, Weaviate, Milvus, or pgvector and states that Qdrant is preferred. fileciteturn4file8L419-L424

### Why

- Docker-friendly.
- Strong metadata filtering.
- Dense vector retrieval.
- Sparse-vector support.
- Persistent volumes.
- Simple operational model.
- Good fit for separate BNS/session retrieval namespaces.

### Logical collections

```text
bns_chunks
user_document_chunks
```

The exact collection/index schema is implementation detail but must preserve corpus/session isolation.

---

# 9. Dense Embedding Model

## D-011 — BAAI/bge-base-en-v1.5

**Classification:** `DECIDED`

### Choice

```text
BAAI/bge-base-en-v1.5
```

The assignment lists this as a suggested open-weight embedding model and explicitly requires self-run/open-weight embeddings. fileciteturn4file8L410-L418

### Why

- Explicitly suggested by the assignment.
- Better retrieval-oriented design than choosing a generic sentence encoder.
- Smaller than `e5-large-v2`.
- Practical for a four-day assignment.
- Suitable for CPU/local execution if GPU resources are unavailable.
- Leaves enough system resources for the rest of the application.

### Required documentation

The implementation must record:

```text
Model:
BAAI/bge-base-en-v1.5

Dimensions:
768

Maximum sequence length:
verify against the exact model configuration used

Query/passage prefix:
verify model-specific usage and record it

Normalization:
record exact implementation behavior
```

The assignment specifically requires these properties to be documented. fileciteturn4file8L414-L418

### Important

The exact runtime model configuration must be inspected when implementation begins. We will not invent model-specific prefix/normalization behavior from memory.

---

# 10. Embedding Execution

## D-012 — Sentence Transformers

**Classification:** `DECIDED`

### Choice

Use the Sentence Transformers ecosystem for the selected embedding model.

### Rules

- Load the model once per worker/process.
- Batch embedding requests.
- Do not reload the model for every document/chunk.
- Log batch size.
- Log embedding throughput.
- Log embedding duration.

---

# 11. Sparse Retrieval

## D-013 — BM25

**Classification:** `DECIDED`

### Choice

Use BM25-compatible sparse retrieval.

The assignment specifically calls out BM25/sparse/full-text because exact legal identifiers are important. fileciteturn4file8L421-L423

### Purpose

Dense retrieval:

```text
"law concerning causing death"
```

Sparse retrieval:

```text
"BNS section 103"
"section 318"
"103(1)"
```

### Decision

The implementation will use a sparse/BM25 representation compatible with the selected Qdrant retrieval architecture.

The exact library/API is an implementation detail and must not change the external retrieval contract.

---

# 12. Hybrid Fusion

## D-014 — Reciprocal Rank Fusion

**Classification:** `DECIDED`

### Choice

```text
RRF
```

### Flow

```text
Dense Results
      │
      ├────────┐
      │        │
      ▼        ▼
Rank List A  Rank List B
      │        │
      └───┬────┘
          ▼
         RRF
          ▼
     Unified Ranking
```

### Why

- Simple.
- Robust when dense and sparse scores have different scales.
- Explicitly accepted by the assignment.
- Easy to evaluate and explain.

---

# 13. Retrieval Top-K

## D-015 — Retrieval Candidate Strategy

**Classification:** `DECIDED`

The system will use separate candidate pools for dense and sparse retrieval, then fuse them.

Baseline:

```text
Dense top-k: 20
Sparse top-k: 20
RRF candidate pool: merged
Final context: evaluated/selected top results
```

### Important

These are **initial implementation values**, not assignment claims.

They may be tuned using the golden-set evaluation.

Any changed values must be recorded here with evaluation evidence.

---

# 14. Reranking

## D-016 — Cross-Encoder Reranker

**Classification:** `BONUS → DECIDED TO DEFER INITIALLY`

The assignment says cross-encoder reranking is optional but heavily rewarded. fileciteturn4file8L423-L426

### Decision

Do not make reranking a prerequisite for the first working retrieval system.

Implementation order:

```text
1. Dense retrieval
2. Sparse retrieval
3. RRF
4. Evaluation
5. Add cross-encoder if time/performance justifies it
```

### Reason

A correct hybrid retrieval system is more important than an unfinished reranking layer.

If implemented, the model and latency impact must be recorded here.

---

# 15. Direct Section Lookup

## D-017 — Deterministic Section Lookup

**Classification:** `MANDATED → IMPLEMENTATION DECISION`

### Choice

Implement a dedicated section-intent detector before semantic retrieval.

Example:

```text
"What is section 103 BNS?"
```

becomes:

```json
{
  "intent": "section_lookup",
  "act": "BNS",
  "section_number": "103"
}
```

Then:

```text
Section Index Lookup
        ↓
Exact section
```

The assignment explicitly requires deterministic section retrieval rather than relying on cosine similarity. fileciteturn4file8L425-L426

---

# 16. Metadata Filtering

## D-018 — Server-Side Metadata Filters

**Classification:** `MANDATED → IMPLEMENTATION DECISION`

Retrieval must support:

```text
act
chapter
specific section
```

Filters will be applied inside the retrieval layer/vector database.

We will not retrieve a broad result set and perform ownership/section filtering only after retrieval.

---

# 17. Chunking Strategy

## D-019 — Legal Structure First

**Classification:** `DECIDED`

### Primary unit

```text
Section
```

### Long section

```text
Section
 ├── subsection
 ├── subsection
 └── clause
```

Split only at:

```text
subsection boundary
clause boundary
```

Never:

```text
mid-sentence
```

The assignment explicitly requires this behavior. fileciteturn4file0L42-L47

---

# 18. Chunk Overlap

## D-020 — Minimal Structural Overlap

**Classification:** `DECIDED`

### Choice

Do not use blind fixed-character overlap.

Use structural context instead.

For a split long section, each child chunk retains sufficient parent metadata/context to identify:

```text
Act
Chapter
Section
Section title
Parent subsection/clause
```

### Why

Generic overlap can duplicate legal text and distort retrieval metrics.

The assignment leaves overlap strategy to the candidate and explicitly requires justification in `DECISIONS.md`. fileciteturn4file0L48-L50

### Decision

Start with:

```text
No arbitrary token overlap.
Parent structural metadata/context is preserved.
```

If evaluation proves contextual overlap is required, add it based on measured results.

---

# 19. Cross-Reference Handling

## D-021 — Detect First, Resolve Later

**Classification:** `MANDATED DETECTION + BONUS RESOLUTION`

### Choice

Store detected references:

```json
"references": [
  "section 2(11)"
]
```

Query-time resolution is deferred unless time permits.

The assignment requires detection/storage and describes query-time resolution as bonus. fileciteturn4file0L51-L52

---

# 20. Confidence / Refusal Strategy

## D-022 — Evidence-Based Refusal

**Classification:** `MANDATED → DECIDED IMPLEMENTATION`

The assignment requires a confidence threshold and refusal path. It explicitly says the bot must not answer from parametric memory when retrieval is insufficient. fileciteturn4file8L427-L434

### Critical decision

We will **not** use an arbitrary universal raw cosine threshold as the final confidence mechanism.

Reason:

```text
Dense cosine score
≠
RRF score
≠
cross-encoder score
```

A threshold only becomes meaningful after evaluating the selected retrieval pipeline.

### Implementation

Use a retrieval-confidence policy composed from:

```text
1. Exact section match where applicable
2. Top retrieved evidence score
3. Agreement between dense and sparse retrieval
4. Presence of relevant statutory metadata
5. Golden-set calibration
```

### Initial policy

```text
Exact section lookup:
    section exists → sufficient evidence

Normal query:
    retrieve candidates
    calculate confidence features
    compare against calibrated threshold

Below threshold:
    refuse
```

The final numeric threshold must be recorded **after evaluation**, not fabricated before the retrieval pipeline exists.

---

# 21. Refusal Behavior

## D-023 — Explicit Refusal

**Classification:** `MANDATED`

When evidence is insufficient:

```text
I don't know based on the available source material.
```

The exact final UI wording may be refined, but the behavior must remain:

```text
No evidence
→ No legal answer
```

The system must not answer unsupported questions using model memory.

---

# 22. Citation Validation

## D-024 — Code-Level Citation Guard

**Classification:** `MANDATED`

### Choice

Implement a deterministic post-generation validator.

```text
Generated answer
      ↓
Extract citations
      ↓
Compare cited sections
against retrieved evidence
      ↓
Valid?
 ├── YES → return
 └── NO → strip/regenerate
```

The assignment explicitly requires this guard in code. fileciteturn4file8L427-L434

---

# 23. Citation Source Drawer

## D-025 — Verbatim Evidence

**Classification:** `MANDATED`

Citation click opens:

```text
Source Drawer
├── Act
├── Section
├── subsection where relevant
├── exact retrieved chunk
└── page number
```

The retrieved text must be shown verbatim.

---

# 24. User Document Corpus

## D-026 — Separate Qdrant Collection

**Classification:** `DECIDED`

Use separate logical collections:

```text
bns_chunks
user_document_chunks
```

User document records additionally contain:

```text
session_id
document_id
```

### Why

This creates an explicit boundary between:

```text
statutory authority
```

and

```text
user-provided evidence
```

The assignment requires that user documents never leak into another user's retrieval and never be confused with the bare act. fileciteturn4file8L437-L445

---

# 25. Session Identity

## D-027 — Anonymous Session Token

**Classification:** `DECIDED`

The assignment permits session-based scoping.

### Choice

Use an anonymous session token rather than requiring account registration for the assignment.

Conceptually:

```text
Browser
  ↓
session_id
  ↓
API
  ↓
document ownership
```

### Storage

The session token is held by the client and associated with server-side application records.

### Security rule

Every document operation checks:

```text
requested_document.session_id
==
current_session_id
```

If not:

```text
404 Not Found
```

This matches the assignment's isolation requirement while avoiding unnecessary authentication scope.

---

# 26. Application Database

## D-028 — PostgreSQL

**Classification:** `DECIDED`

### Choice

```text
PostgreSQL
```

Use it for application metadata, not as the primary dense vector store.

### Stores

```text
sessions
conversations
messages
documents
ingestion_jobs
feedback
forms metadata
```

### Why

- Production-grade relational persistence.
- Strong constraints.
- Clear ownership relationships.
- Good fit for application metadata.
- Docker-compatible.

Qdrant remains the retrieval system.

---

# 27. File Storage

## D-029 — Local Persistent Storage

**Classification:** `DECIDED`

For the assignment deployment:

```text
Docker named volume
```

stores uploaded documents and generated forms where persistent storage is required.

Conceptually:

```text
/storage
├── documents/
└── forms/
```

### Why

The assignment requires Docker Compose and does not require cloud object storage.

Cloud object storage would add deployment complexity without directly increasing assignment compliance.

---

# 28. Async Queue

## D-030 — arq + Redis

**Classification:** `DECIDED`

### Choice

```text
Redis
+
arq
```

### Why

- Async-native Python.
- Fits FastAPI.
- Lightweight.
- Simple Docker Compose topology.
- Appropriate for document ingestion jobs.

### Worker responsibilities

```text
parse
chunk
embed
index
status updates
```

---

# 29. Streaming Transport

## D-031 — Server-Sent Events

**Classification:** `DECIDED`

### Choice

```text
SSE
```

### Why

The assignment allows SSE or WebSocket.

Chat is primarily one-way server-to-client streaming after a request, making SSE simpler than WebSocket.

### Flow

```text
POST /chat
      ↓
stream response events
      ↓
Frontend EventSource/fetch stream
```

The frontend must progressively render tokens.

---

# 30. LLM Provider Abstraction

## D-032 — Provider Interface

**Classification:** `MANDATED BEHAVIOR → DECIDED DESIGN`

The assignment requires the provider to be swappable by environment variable.

### Choice

```text
LLMProvider
├── generate()
├── stream()
└── metadata()
```

Provider implementation is selected through configuration.

Conceptually:

```text
LLM_PROVIDER=ollama
```

or another supported provider.

### Rule

Business logic must never import a specific hosted provider directly.

---

# 31. Ollama

## D-033 — Ollama Evaluation Path

**Classification:** `MANDATED`

The assignment requires a keyless evaluation path through Ollama.

### Decision

Ollama is treated as a first-class provider option.

This allows reviewers to evaluate the system without receiving our API key.

---

# 32. Generation Provider

## D-034 — Hosted Generation Allowed

**Classification:** `MANDATED OPTION`

The assignment allows generation through a hosted API while explicitly prohibiting hosted embeddings.

### Decision

Generation provider may be hosted in the deployment environment, but:

```text
Embedding = local/open-weight
Generation = provider abstraction
```

The exact hosted provider is environment configuration rather than application architecture.

---

# 33. Forms Extraction

## D-035 — PyMuPDF-First

**Classification:** `DECIDED`

### Choice

Use a PDF library capable of:

- reading source pages,
- detecting/extracting text,
- copying page content,
- preserving PDF structure where possible.

### Principle

Do not rasterize every page by default.

The assignment requires page-perfect extraction and says rasterization should be used only as fallback. fileciteturn4file0L11-L16

---

# 34. OCR

## D-036 — Tesseract Fallback

**Classification:** `MANDATED FALLBACK → DECIDED IMPLEMENTATION`

### Choice

```text
Normal text extraction
        ↓
Text quality check
        ↓
Bad/missing text?
        ↓
Tesseract OCR
```

OCR is not the primary extraction method.

Pages requiring OCR are logged.

---

# 35. Form Title Extraction

## D-037 — Programmatic Extraction

**Classification:** `MANDATED`

Never hardcode the list of form titles.

Pipeline:

```text
Page
 ↓
Detect form number/title
 ↓
Normalize title for filename only
 ↓
Store exact scraped title in manifest
```

The assignment explicitly makes hardcoded form titles a rejection condition.

---

# 36. Form Naming

## D-038 — Deterministic Slugifier

**Classification:** `MANDATED → DECIDED IMPLEMENTATION`

Format:

```text
FORM-<number>_<slugified-title>.pdf
```

The slugifier must be:

- deterministic,
- filesystem-safe,
- space-free,
- collision-safe.

The manifest stores the exact scraped title separately from the filename.

---

# 37. Form Multi-Page Detection

## D-039 — Content/Structure-Based Grouping

**Classification:** `MANDATED`

A multi-page form remains one PDF.

Do not assume:

```text
1 page = 1 form
```

The detector uses form identifiers, title patterns, continuation indicators, and page structure.

---

# 38. Forms Manifest

## D-040 — JSON Manifest

**Classification:** `MANDATED`

Required fields:

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

Uncertain extraction:

```text
needs_review = true
```

---

# 39. Forms Idempotency

## D-041 — Deterministic Rebuild

**Classification:** `MANDATED`

Same source:

```text
Run 1
=
Run 2
```

Expected:

- same form boundaries,
- same filenames,
- same bytes,
- same SHA-256,
- no duplicate metadata rows.

---

# 40. Conversation Persistence

## D-042 — PostgreSQL Conversation Store

**Classification:** `DECIDED`

Conversation history is persisted in PostgreSQL.

Logical model:

```text
Session
  └── Conversation
       └── Messages
```

Required operations:

```text
create
list
rename
delete
append
read
```

---

# 41. Rate Limiting

## D-043 — Redis-Backed Rate Limiting

**Classification:** `MANDATED → DECIDED IMPLEMENTATION`

Use Redis for rate-limit counters.

Apply limits to at least:

```text
chat
upload
```

Exact numeric limits will be configuration values.

They must be documented in `.env.example` and tuned based on deployment requirements.

---

# 42. Structured Logging

## D-044 — JSON Logs

**Classification:** `MANDATED`

All services use structured JSON logs.

Every request gets:

```text
request_id
```

The identifier propagates through:

```text
API
 ↓
retrieval
 ↓
generation
```

and relevant worker operations.

---

# 43. Observability

## D-045 — Prometheus Metrics

**Classification:** `MANDATED`

Expose:

```text
GET /api/v1/metrics
```

Required measurements include:

- request count,
- latency,
- embedding time,
- retrieval latency,
- vector DB state,
- token usage,
- upload count,
- refusal count.

The assignment explicitly requires Prometheus-format metrics and an observability deliverable. fileciteturn4file5L261-L281

---

# 44. Grafana Decision

## D-046 — Metrics-First, Grafana if Time Permits

**Classification:** `DECIDED`

The assignment allows either:

```text
Grafana dashboard
```

or

```text
documented /metrics scraping + screenshots
```

### Decision

Primary guaranteed deliverable:

```text
Prometheus-compatible /metrics
+
documented scraping
+
screenshots
```

Grafana will be added if time permits.

This avoids making the assignment depend on an additional visualization service.

---

# 45. Evaluation Dataset

## D-047 — 25–30 Golden Questions

**Classification:** `MANDATED`

Create:

```text
eval/golden_set.jsonl
```

with:

```text
25–30 questions
```

and at least:

```text
5 out-of-scope/refusal questions
```

The assignment explicitly requires this. fileciteturn4file5L263-L281

---

# 46. Evaluation Configuration Comparison

## D-048 — Dense-Only vs Hybrid Baseline Comparison

**Classification:** `DECIDED`

The assignment requires at least two configurations.

We will compare:

```text
Configuration A:
Dense retrieval

Configuration B:
Dense + sparse + RRF
```

### Why

This directly demonstrates why hybrid retrieval is necessary for legal identifiers and is easy for the reviewer to understand.

The final README will contain numerical:

```text
Recall@5
Recall@10
MRR
Citation accuracy
Refusal rate
p50
p95
Retrieval latency
Generation latency
```

The assignment explicitly requires numerical comparison rather than adjectives. fileciteturn4file5L273-L281

---

# 47. Testing Strategy

## D-049 — pytest

**Classification:** `DECIDED`

Backend:

```text
pytest
```

Tests include:

```text
unit
integration
API
retrieval
end-to-end
```

Frontend tests may use the React ecosystem's standard test tooling.

---

# 48. Coverage Threshold

## D-050 — Explicit CI Threshold

**Classification:** `MANDATED → DECIDED`

The assignment requires PR failure below a stated threshold.

### Decision

Initial target:

```text
80% coverage
```

This is an engineering quality target, not an assignment-provided number.

CI must fail if coverage drops below the configured threshold.

---

# 49. CI Platform

## D-051 — GitHub Actions

**Classification:** `MANDATED`

CI uses:

```text
GitHub Actions
```

Triggers:

```text
pull_request
push to main
```

---

# 50. Secret Scanning

## D-052 — Gitleaks

**Classification:** `DECIDED`

Use:

```text
Gitleaks
```

The assignment permits Gitleaks or TruffleHog and requires CI failure when a credential appears in the diff. fileciteturn4file5L235-L239

---

# 51. Container Vulnerability Scanning

## D-053 — Trivy

**Classification:** `MANDATED`

Use:

```text
Trivy
```

to scan the built Docker image.

---

# 52. Container Registry

## D-054 — GHCR

**Classification:** `MANDATED`

Push Docker images to:

```text
GitHub Container Registry
```

Tag with:

```text
commit SHA
```

---

# 53. Deployment

## D-055 — Docker Compose Backend Stack

**Classification:** `MANDATED`

Backend runtime:

```text
API
Worker
Redis
Qdrant
PostgreSQL
```

through Docker Compose.

The assignment requires backend + vector DB + worker through Compose with health checks, named volumes, shared network, and restart policies. fileciteturn4file5L245-L253

---

# 54. Frontend Deployment

## D-056 — Vercel

**Classification:** `MANDATED FOR DEVOPS / OPTIONAL OTHERWISE`

For the DevOps track:

```text
Frontend → Vercel
```

For non-DevOps tracks, deployment is optional according to the assignment. fileciteturn4file5L245-L247

---

# 55. Self-Hosted Runner

## D-057 — Track-Dependent

**Classification:** `MANDATED FOR DEVOPS`

For the DevOps track:

```text
Self-hosted GitHub Actions runner
```

must handle at least build-and-deploy.

Documentation must cover:

- provisioning,
- labels,
- service installation,
- token handling,
- fork PR hardening,
- evidence of runner execution.

fileciteturn4file5L240-L244

---

# 56. Docker User

## D-058 — Non-Root Runtime

**Classification:** `DECIDED QUALITY REQUIREMENT`

Application containers run as non-root users.

This is part of our production-quality baseline.

---

# 57. Docker Images

## D-059 — Multi-Stage Slim Builds

**Classification:** `DECIDED`

Use:

```text
multi-stage Dockerfiles
slim base images
pinned dependencies
.dockerignore
```

Do not ship:

```text
.git
.env
node_modules
raw PDFs
```

inside build context/image unnecessarily.

---

# 58. Bootstrap Strategy

## D-060 — One-Shot Idempotent Bootstrap

**Classification:** `MANDATED`

Use:

```text
scripts/bootstrap.sh
```

as the documented entry point.

It orchestrates:

```text
BNS ingestion
+
forms extraction
```

The assignment explicitly requires a documented one-shot script or idempotent init container. fileciteturn4file5L247-L253

### Rule

`docker-compose up` starts the application.

Bootstrap performs one-time data preparation.

It must not re-embed the complete BNS corpus every time a service restarts.

---

# 59. Rollback

## D-061 — Image SHA Rollback

**Classification:** `MANDATED → DECIDED`

Images are immutable by commit SHA.

Rollback:

```text
current SHA
   ↓
previous known-good SHA
   ↓
redeploy
```

The README must state:

- how to select the previous image,
- how to restart Compose,
- what persistent data remains,
- expected recovery process.

---

# 60. Secrets

## D-062 — No Secrets in Repository

**Classification:** `MANDATED`

Never commit:

```text
.env
API keys
credentials
tokens
private keys
```

The assignment states that a committed key is an immediate rejection condition. fileciteturn4file5L254-L260

Secrets:

```text
Local → .env
CI → GitHub Secrets
Vercel → Vercel project env vars
```

---

# 61. Accidental Secret Handling

## D-063 — Rotate and Disclose

**Classification:** `MANDATED`

If a credential is accidentally committed:

```text
1. Rotate/revoke credential.
2. Do not pretend it never happened.
3. Document incident in DECISIONS.md.
4. Prevent further exposure.
```

Do not rewrite history to conceal the incident.

This follows the assignment's explicit instruction. fileciteturn4file5L254-L260

---

# 62. Database Boundary

The system intentionally uses different persistence technologies for different responsibilities.

```text
PostgreSQL
 └── application state / metadata

Qdrant
 └── retrieval indexes

Redis
 └── queue + transient counters

Filesystem / named volume
 └── PDFs / generated forms
```

This avoids forcing one database to perform unrelated responsibilities.

---

# 63. Why Not Use PostgreSQL/pgvector for Everything?

The assignment permits pgvector, but explicitly prefers Qdrant.

Decision:

```text
PostgreSQL = application metadata
Qdrant = retrieval
```

This keeps retrieval concerns isolated and follows the assignment's preferred vector-store direction.

---

# 64. Why Not Microservices?

## D-064 — Modular Monolith

**Classification:** `DECIDED`

Nyaya will be a **modular monolith at the application level** with separate runtime worker/container processes.

Logical modules:

```text
API
├── chat
├── documents
├── forms
├── search
├── feedback
├── health

Domain modules
├── ingestion
├── retrieval
├── llm
├── forms
└── workers
```

### Why

The assignment has a four-day deadline.

Full microservices would add:

- deployment complexity,
- network failure modes,
- more CI/CD work,
- more operational overhead,

without directly improving the scored requirements.

---

# 65. Why Not Build Extra Product Features?

## D-065 — Assignment Scope Lock

**Classification:** `DECIDED`

Do not add unrelated features before completing mandatory requirements.

Examples of deferred features:

```text
user accounts
billing
admin dashboards
mobile native apps
legal case management
calendar
notifications
advanced analytics
```

The assignment says to attempt maximum task coverage and honestly document incomplete work. fileciteturn4file2L125-L133

### Rule

Mandatory assignment requirements > bonus requirements > polish > unrelated features.

---

# 66. Decision Priority During Four-Day Build

Implementation priority:

```text
P0 — Release blockers
    secrets
    public repo
    README

P1 — Core legal correctness
    structure-aware ingestion
    hybrid retrieval
    direct section lookup
    citation validation
    refusal

P2 — Forms
    extraction
    title scraping
    multi-page grouping
    manifest

P3 — Product
    chat UI
    upload UI
    Forms UI
    streaming

P4 — Backend/Infrastructure
    APIs
    worker
    Docker
    health
    logging

P5 — CI/CD
    tests
    coverage
    Gitleaks
    Docker
    Trivy
    GHCR
    deployment

P6 — Evaluation
    golden set
    metrics
    configuration comparison

P7 — Bonus
    reranking
    cross-reference resolution
    Grafana
```

---

# 67. Known Trade-Offs

## Trade-Off 1 — Qdrant + PostgreSQL

**Benefit:**

Clear separation between retrieval and application state.

**Cost:**

Two persistent databases instead of one.

**Decision:**

Accept the additional service because Qdrant is preferred by the assignment and retrieval is the heart of Part A.

---

## Trade-Off 2 — React/Vite instead of Next.js

**Benefit:**

Smaller frontend architecture and simpler deployment.

**Cost:**

No Next.js server-side features.

**Decision:**

Those features are not required for the assignment.

---

## Trade-Off 3 — Anonymous Sessions

**Benefit:**

No authentication product scope.

**Cost:**

Less persistent identity and weaker account-level management.

**Decision:**

Session isolation is sufficient for the assignment's user-document requirement.

---

## Trade-Off 4 — No Initial Reranker

**Benefit:**

Faster path to a correct hybrid retriever.

**Cost:**

Potentially lower ranking quality.

**Decision:**

Implement after the baseline retrieval system if time permits.

---

## Trade-Off 5 — Local Form Storage

**Benefit:**

Simple and deterministic.

**Cost:**

Not horizontally scalable across arbitrary machines.

**Decision:**

Acceptable for assignment deployment; cloud object storage is future work.

---

## Trade-Off 6 — Modular Monolith

**Benefit:**

Fast development and easy local deployment.

**Cost:**

Less independent service scaling.

**Decision:**

Appropriate for the four-day assignment and small product scope.

---

# 68. What We Will Not Claim

To prevent overengineering and false claims:

### We will not claim:

```text
"AI understands the law."
```

We will say:

```text
The assistant generates answers from retrieved source evidence
and validates citations against that evidence.
```

### We will not claim:

```text
"Hallucination-free."
```

We will say:

```text
The system implements retrieval thresholds, refusal behavior,
and post-generation citation validation.
```

### We will not claim:

```text
"Production-ready for real legal advice."
```

The UI will contain the required:

```text
not legal advice
```

disclaimer.

---

# 69. Known Weaknesses to Track

These are expected engineering risks, not hidden problems.

| Area | Risk | Mitigation |
|---|---|---|
| PDF parsing | Government PDF layout may be irregular | Fixtures + page inspection + OCR fallback |
| Section detection | Formatting may vary | Structure-aware parser + tests |
| Retrieval | Indirect legal questions may retrieve poorly | Hybrid retrieval + evaluation |
| Confidence | Scores are model/configuration-dependent | Golden-set calibration |
| Citation validation | Text citation extraction can be imperfect | Deterministic parser + regeneration |
| OCR | OCR can introduce text errors | `needs_review` + confidence |
| Anonymous sessions | Browser token loss loses ownership context | Clear session lifecycle |
| Local storage | Not horizontally scalable | Document as future enhancement |
| No initial reranker | Some difficult ranking cases may remain | Evaluate and add if time permits |
| Hosted LLM | Provider availability/latency | Ollama fallback |

---

# 70. Future Work — Explicitly Deferred

These are not part of the initial assignment implementation unless mandatory work is complete.

```text
1. Cross-encoder reranking
2. Query-time cross-reference graph resolution
3. Cloud object storage
4. Full authentication/account system
5. Horizontal worker scaling
6. Advanced tracing
7. Grafana dashboards
8. More sophisticated query classification
9. Multilingual legal retrieval
10. Advanced OCR/layout understanding
```

The assignment asks for a statement of what would change with two more weeks; this list will be refined after implementation based on actual weaknesses.

---

# 71. Decision Change Protocol

Any architectural change must follow:

```text
Identify problem
      ↓
Identify affected requirement
      ↓
Determine whether assignment mandates current behavior
      ↓
Propose alternative
      ↓
Record trade-off
      ↓
Update DECISIONS.md
      ↓
Update ARCHITECTURE.md if needed
      ↓
Update tests
      ↓
Implement
```

The coding agent must never silently alter an architectural decision.

---

# 72. Final Locked Stack

Unless a documented decision change occurs:

| Layer | Decision |
|---|---|
| Frontend | React + TypeScript + Vite |
| Styling | Tailwind CSS |
| Backend | FastAPI + Python |
| Application DB | PostgreSQL |
| Vector DB | Qdrant |
| Sparse retrieval | BM25/sparse retrieval |
| Hybrid fusion | RRF |
| Dense embeddings | `BAAI/bge-base-en-v1.5` |
| Embedding runtime | Sentence Transformers |
| Queue | Redis + arq |
| Streaming | SSE |
| LLM | Provider abstraction + Ollama path |
| File storage | Persistent Docker volume |
| PDF parsing | PyMuPDF-first |
| OCR | Tesseract fallback |
| Forms manifest | JSON |
| Backend container | Multi-stage slim image |
| Frontend container | Multi-stage build |
| CI | GitHub Actions |
| Secret scan | Gitleaks |
| Image scan | Trivy |
| Registry | GHCR |
| Deployment | Docker Compose |
| DevOps frontend | Vercel |
| DevOps runner | Self-hosted GitHub Actions runner |
| Metrics | Prometheus format |
| Testing | pytest + frontend test tooling |
| Coverage target | 80% |
| Evaluation | 25–30 golden questions |
| Comparison | Dense-only vs hybrid |

---

# 73. Final Architecture Lock

Before vibecoding begins, the following are considered locked:

- [x] Assignment source corpus.
- [x] Structure-aware ingestion.
- [x] Legal section atomicity.
- [x] Dense + sparse hybrid retrieval.
- [x] RRF fusion.
- [x] Deterministic section lookup.
- [x] Citation validation in code.
- [x] Evidence-based refusal.
- [x] Separate BNS/user-document retrieval.
- [x] Session isolation.
- [x] Untrusted-document boundary.
- [x] Qdrant.
- [x] PostgreSQL.
- [x] Redis + arq.
- [x] FastAPI.
- [x] React + TypeScript + Vite.
- [x] Tailwind.
- [x] SSE.
- [x] BGE base embedding model.
- [x] PyMuPDF-first forms extraction.
- [x] Tesseract fallback.
- [x] Programmatic form-title extraction.
- [x] Deterministic multi-page form generation.
- [x] Forms manifest.
- [x] Docker Compose.
- [x] GitHub Actions.
- [x] Gitleaks.
- [x] Trivy.
- [x] GHCR.
- [x] Prometheus-compatible metrics.
- [x] Golden-set evaluation.
- [x] Dense-only vs hybrid comparison.

**From this point onward, the implementation agent should treat this file as the engineering decision boundary.**

Any deviation must be explicit, justified, documented, and reflected in the affected architecture/tests.
# 74. Source Discrepancy: Supplied PDF is BNSS, not BNS (2026-08-30)

The assignment-supplied file `data/raw/BNS_bare_act_2023.pdf` was inspected
(sha256 `5e60e2afe30d0fe7eca4f8126301146b76c86a444e690581f81eb564843517fe`,
249 pages). Page 1 reads "THE BHARATIYA NAGARIK SURAKSHA SANHITA, 2023 / NO. 46
OF 2023 / An Act to consolidate and amend the law relating to Criminal
Procedure" — this is **BNSS** (criminal procedure), not the required **BNS**
(substantive penal law, No. 45 of 2023, 358 sections). The file name does not
match its contents.

Decision (per project owner, pending clarification from DhronAI):

- Do NOT download or substitute any other legal PDF (SRC-002/SRC-003).
- Do NOT rename or redefine the current file as BNS; the assignment corpus
  remains BNS. Final BNS corpus validation is **BLOCKED** until the correct
  source is confirmed.
- The BNSS PDF is used strictly as a temporary development fixture: it shares
  the Gazette layout (marginal notes, chrome, printed page numbers), so it
  exercises the real ingestion pipeline while the correct source is awaited.
- The pipeline is spec-driven (`CorpusSpec`): swapping in the real BNS PDF and
  rerunning `scripts/ingest.py --spec bns` requires no application-code
  changes. The pipeline rejects the BNSS file under the BNS spec
  (content-based act-title validation, never filename).

# 75. Ingestion Heuristics (2026-08-30)

- **Hyphenated line wraps (A1-034):** a trailing hyphen before a
  lowercase-continuation line is merged, *keeping the hyphen*. Removing it
  would alter statutory wording ("Sub-registrar" → "Subregistrar"), which
  legal-integrity rules forbid.
- **Marginal-note titles (A1-033/A1-036):** Gazette marginal notes surface in
  the text layer as short note-like lines interleaved after sentence-terminal
  lines or at page tail. Clusters (final line ending ".") are associated in
  order with untitled sections; pure citation clusters ("1 of 1871.") are
  dropped. Uncertain associations set `title_confident=False` /
  `needs_review=True` and emit warnings — never silently guessed.
- **Overlap strategy (A1-029):** no arbitrary character overlap (D-020).
  Split chunks carry full parent metadata (act/chapter/section/pages) as
  context; oversized units without a subsection boundary stay whole with a
  warning.
- **Cross-references (A1-037/038):** detected at ingestion, stored as
  normalized strings in `references[]`; resolution deferred to query time
  (D-021).
- **Embedding/index seams:** `EmbeddingProvider` and `ChunkIndex` protocols
  are the Phase 3 integration points (BGE + Qdrant wired, deterministic JSONL
  sink default).
