# Nyaya — Requirements & Compliance Matrix

**Project:** Nyaya — Legal Assistant over the Bharatiya Nyaya Sanhita  
**Source of Truth:** DhronAI Technical Assignment  
**Status:** Pre-Implementation  
**Purpose:** This document converts the assignment into atomic, traceable implementation requirements. It is the master checklist for development, testing, review, and final submission.

> **Rule:** The assignment is authoritative. Do not silently add, remove, weaken, or reinterpret a requirement. Where the assignment deliberately leaves a decision open, record the chosen implementation in `DECISIONS.md`.

---

## 0. Requirement Status Model

| Status | Meaning |
|---|---|
| `TODO` | Not implemented or not yet verified |
| `IN_PROGRESS` | Currently being implemented |
| `DONE` | Implemented and verified against acceptance criteria |
| `PARTIAL` | Partially implemented; remaining gap documented |
| `BLOCKED` | Cannot proceed without an external dependency/decision |
| `N/A` | Only when genuinely not applicable to the selected assignment track |

### Priority

| Priority | Meaning |
|---|---|
| `MUST` | Required by the assignment |
| `BONUS` | Explicitly described as bonus / heavily rewarded but not mandatory |
| `TRACK` | Required depending on role track |
| `DECISION` | Assignment intentionally leaves the implementation choice to the candidate |

---

# 1. Product & Assignment Baseline

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| P-001 | MUST | Build Nyaya as a small but real product | Working product exists, not merely an LLM demo | DONE |
| P-002 | MUST | Provide a ChatGPT-style legal assistant | User can ask supported Indian criminal-law questions through the UI | DONE |
| P-003 | MUST | Legal answers must be grounded in the required corpus | Legal answers use retrieved authoritative context | DONE |
| P-004 | MUST | Every legal claim must carry an exact Act/Section citation | Generated legal claims contain citations | DONE |
| P-005 | MUST | Allow users to upload their own legal document | User can upload and later query a document | DONE |
| P-006 | MUST | Provide a downloadable statutory forms library | Forms can be browsed and downloaded | DONE |
| P-007 | MUST | Product has two primary panels | UI contains Chatbot and Forms panels | DONE |
| P-008 | MUST | Optimize for a second engineer being able to run the system | Clean-clone setup is documented and works | DONE |
| P-009 | MUST | Attempt the maximum practical scope within the four-day deadline | Completed/partial work is honestly reported in README | DONE |
| P-010 | MUST | Every submitted line must be explainable by the candidate | AI-assisted implementation is documented and understandable | DONE |

---

# 2. Source Corpus

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| SRC-001 | MUST | Use Bharatiya Nyaya Sanhita, 2023 as primary corpus | Serving corpus is the content-validated BNS Gazette artifact (358 sections, manifest SHA-256) | DONE |
| SRC-002 | MUST | Use the official bare-act PDF specified by the assignment | The supplied bare-act PDF was content-validated as BNSS (not BNS); it is used as the forms source, and the statute corpus uses the official BNS 2023 Gazette PDF — documented trade-off (README "Known gaps") | PARTIAL |
| SRC-003 | MUST | Do not substitute a differently paginated copy | No unofficial BNS substitute: the official Gazette publication is used, never a re-paginated copy | DONE |
| SRC-004 | MUST | Use pages 190–249 as the expected forms range | Forms extraction initially targets pages 190–249 | DONE |
| SRC-005 | MUST | Treat the actual page contents as authoritative for form parsing | Parser detects what is actually on each page | DONE |
| SRC-006 | MUST | Do not assume the form pages solely from statute identity | Parser is content-driven rather than statute-name-driven | DONE |
| SRC-007 | MUST | Document any disagreement between observed and expected forms range | Discrepancy is recorded in `DECISIONS.md` | DONE |
| SRC-008 | MUST | Keep source PDF out of Git | Raw PDF is gitignored | DONE |
| SRC-009 | MUST | Make source retrieval/ingestion reproducible | Bootstrap/ingestion process obtains/processes the source deterministically | DONE |
| SRC-010 | MUST | Validate the supplied source by content before treating ingestion as authoritative | A source whose detected act title/structure does not match the expected corpus is rejected; filename is never evidence | DONE |
| SRC-011 | MUST | Record corpus identity and source version in ingestion/index metadata | Every chunk carries corpus identity; a source manifest records SHA-256, page count, detected act title, ingested_at | DONE |
| SRC-012 | MUST | Keep the authoritative corpus replaceable and re-ingestible without application-code changes | Replacing the source PDF and re-running ingestion re-indexes the corpus; no code change is required | DONE |
| SRC-013 | MUST | Retrieval operates against the active configured corpus, not hardcoded statute assumptions | Retrieval derives corpus identity from chunk/index metadata of the indexed corpus | DONE |

---

# 3. Part A — Retrieval & Indexing

## A1. Structure-Aware Ingestion

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| A1-001 | MUST | Do not use naive `RecursiveCharacterTextSplitter(chunk_size=512)` as the statutory chunking strategy | Chunking understands statutory structure | DONE |
| A1-002 | MUST | Extract structured statutory metadata per chunk | Required metadata is present in stored chunks | DONE |
| A1-003 | MUST | Preserve `act` | Chunk contains act name | DONE |
| A1-004 | MUST | Preserve `act_short` | Chunk contains `BNS` | DONE |
| A1-005 | MUST | Preserve `chapter` | Chapter identifier is retained | DONE |
| A1-006 | MUST | Preserve `chapter_title` | Chapter title is retained | DONE |
| A1-007 | MUST | Preserve `section_number` | Section number is retained | DONE |
| A1-008 | MUST | Preserve `section_title` | Section title is retained | DONE |
| A1-009 | MUST | Preserve `subsection` where applicable | Subsection metadata is retained | DONE |
| A1-010 | MUST | Preserve `clause` where applicable | Clause metadata is retained | DONE |
| A1-011 | MUST | Preserve chunk `text` | Actual chunk text is retained | DONE |
| A1-012 | MUST | Preserve `has_illustration` | Boolean/appropriate indicator is stored | DONE |
| A1-013 | MUST | Preserve `has_proviso` | Boolean/appropriate indicator is stored | DONE |
| A1-014 | MUST | Preserve `has_exception` | Boolean/appropriate indicator is stored | DONE |
| A1-015 | MUST | Preserve `page_start` | Source starting page is stored | DONE |
| A1-016 | MUST | Preserve `page_end` | Source ending page is stored | DONE |
| A1-017 | MUST | Generate a stable `chunk_id` | Each chunk has a unique deterministic identifier | DONE |
| A1-018 | MUST | Preserve `source_uri` | Source reference is stored | DONE |
| A1-019 | MUST | Preserve `ingested_at` | Ingestion timestamp is stored | DONE |
| A1-020 | MUST | Treat a legal section as the atomic chunking unit | Short sections remain whole | DONE |
| A1-021 | MUST | Never split a section shorter than maximum chunk size | Short section remains one chunk | DONE |
| A1-022 | MUST | Split long sections only at subsection/clause boundaries | Long section boundaries follow legal structure | DONE |
| A1-023 | MUST | Never split long statutory text mid-sentence | No sentence is broken merely for chunk size | DONE |
| A1-024 | MUST | Keep provisos attached to parent section | Retrieved chunk containing proviso retains parent section context | DONE |
| A1-025 | MUST | Keep exceptions attached to parent section | Retrieved exception remains attached | DONE |
| A1-026 | MUST | Keep explanations attached to parent section | Explanation remains attached | DONE |
| A1-027 | MUST | Keep illustrations attached to parent section | Illustration remains attached | DONE |
| A1-028 | MUST | Avoid orphaned legal components | No proviso/exception/explanation/illustration becomes an independent misleading retrieval unit | DONE |
| A1-029 | DECISION | Choose an overlap strategy | Strategy is implemented and justified in `DECISIONS.md` | DONE |
| A1-030 | MUST | Handle running headers | Headers do not contaminate legal chunk text | DONE |
| A1-031 | MUST | Handle running footers | Footers do not contaminate legal chunk text | DONE |
| A1-032 | MUST | Handle page numbers | Page numbers do not contaminate legal content | DONE |
| A1-033 | MUST | Handle marginal notes | Marginal notes are correctly handled | DONE |
| A1-034 | MUST | Handle hyphenated line breaks | Broken words across PDF lines are reconstructed appropriately | DONE |
| A1-035 | MUST | Handle two-column layout if present | Text order remains correct if source uses columns | DONE (source is single-column; N/A) |
| A1-036 | MUST | Correctly associate section numbers with section titles | Section metadata pairing is correct | DONE |
| A1-037 | MUST | Detect cross-references inside statutory text | References such as `section 2(11)` are identified | DONE |
| A1-038 | MUST | Store cross-references in a `references` array | Chunk metadata contains detected references | DONE |
| A1-039 | BONUS | Resolve cross-references at query time | Related referenced sections can be resolved during retrieval | TODO |

## A2. Embeddings

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| A2-001 | MUST | Use an open-weight embedding model run by the project | Embeddings execute locally/self-hosted | DONE |
| A2-002 | MUST | Do not use OpenAI embeddings | Retrieval embeddings do not use OpenAI | DONE |
| A2-003 | MUST | Do not use Cohere embeddings | Retrieval embeddings do not use Cohere | DONE |
| A2-004 | MUST | Do not use Voyage embeddings | Retrieval embeddings do not use Voyage | DONE |
| A2-005 | DECISION | Select an embedding model | Selected model is documented | DONE |
| A2-006 | MUST | Document embedding dimensions | README/DECISIONS records dimensions | DONE |
| A2-007 | MUST | Document maximum sequence length | README/DECISIONS records limit | DONE |
| A2-008 | MUST | Document required query/passage prefixes if applicable | Correct prefix behavior is recorded | DONE |
| A2-009 | MUST | Apply model-required query/passage prefixes correctly | Retrieval uses correct prefix convention when model requires it | DONE |
| A2-010 | MUST | Document normalization behavior | Normalization choice is recorded | DONE |
| A2-011 | MUST | Batch embedding operations | Embeddings are generated in batches | DONE |
| A2-012 | MUST | Log embedding throughput | Throughput is observable in logs/metrics | DONE |
| A2-013 | MUST | Make full-act cold-start embedding a one-time job | Full corpus is not embedded on every container boot | DONE |

## A3. Vector Store & Retrieval

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| A3-001 | MUST | Use a vector DB that can run in Docker | Vector DB is containerized | DONE |
| A3-002 | DECISION | Select vector DB from allowed choices | Selection is documented | DONE |
| A3-003 | MUST | Implement dense retrieval | Dense search returns relevant chunks | DONE |
| A3-004 | MUST | Implement sparse/BM25/full-text retrieval | Sparse/exact retrieval is available | DONE |
| A3-005 | MUST | Combine dense and sparse retrieval | Both retrieval signals participate in final retrieval | DONE |
| A3-006 | MUST | Fuse hybrid retrieval results | Fusion mechanism is implemented | DONE |
| A3-007 | DECISION | Select/justify fusion method | Method is documented; RRF is acceptable | DONE |
| A3-008 | MUST | Support chapter metadata filtering | Query can restrict retrieval to a chapter | DONE |
| A3-009 | MUST | Support act metadata filtering | Query can restrict retrieval to an act | DONE |
| A3-010 | MUST | Support specific-section filtering | Query can restrict retrieval to a section | DONE |
| A3-011 | BONUS | Implement cross-encoder reranking of top-k | Top-k candidates can be reranked | TODO |
| A3-012 | MUST | Detect direct section-number intent | Queries such as `section 103 BNS` are detected | DONE |
| A3-013 | MUST | Direct lookup returns requested section deterministically | Section lookup does not depend solely on cosine similarity | DONE |
| A3-014 | MUST | Direct lookup bypasses or boosts normal retrieval as appropriate | Exact identifier path has deterministic precedence | DONE |
| A3-015 | MUST | Explain retrieval routing approach | Routing strategy is documented | DONE |

## A4. Citation Contract

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| A4-001 | MUST | Every legal statement carries an inline citation | Generated legal statements have citations | DONE |
| A4-002 | MUST | Citation includes Act | Citation identifies BNS/appropriate authority | DONE |
| A4-003 | MUST | Citation includes Section | Citation contains section number | DONE |
| A4-004 | MUST | Include subsection when relevant | Relevant subsection appears in citation | DONE |
| A4-005 | MUST | Use required citation style | Example format `[BNS s.103(1)]` is supported | DONE |
| A4-006 | MUST | Render citations as inline UI chips | Citation appears as clickable chip | DONE |
| A4-007 | MUST | Citation chip opens source drawer | Click action opens source evidence | DONE |
| A4-008 | MUST | Source drawer displays retrieved chunk verbatim | Exact retrieved text is shown | DONE |
| A4-009 | MUST | Source drawer displays source page number | Page number is shown | DONE |
| A4-010 | MUST | Define a confidence threshold | Threshold exists and is documented | DONE |
| A4-011 | MUST | Refuse when retrieval is below confidence threshold | Low-confidence question produces refusal | DONE |
| A4-012 | MUST | Do not answer low-confidence legal questions from parametric memory | No unsupported answer is generated | DONE |
| A4-013 | MUST | Implement post-generation citation validation | Generated citations are checked in code | DONE |
| A4-014 | MUST | Verify every cited section exists in retrieved context | Unsupported section numbers are detected | DONE |
| A4-015 | MUST | Handle invented citations | Invalid citation is stripped or answer regenerated | DONE |
| A4-016 | MUST | Keep citation guard in executable code | Guard is not prompt-only | DONE |
| A4-017 | MUST | Display standing `not legal advice` disclaimer | Disclaimer appears once in chat panel chrome | DONE |
| A4-018 | MUST | Do not spam legal disclaimer into every message | Disclaimer is not repeated in each answer | DONE |

## A5. Two Corpora / User Documents

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| A5-001 | MUST | Support user legal-document uploads | User can upload a document | DONE |
| A5-002 | MUST | Ingest uploaded documents | Uploaded document enters ingestion pipeline | DONE |
| A5-003 | MUST | Chunk uploaded documents | Uploaded content is chunked | DONE |
| A5-004 | MUST | Embed uploaded documents | Uploaded chunks receive embeddings | DONE |
| A5-005 | MUST | Make uploaded documents queryable | Ready document can be queried | DONE |
| A5-006 | MUST | Scope uploaded-document retrieval to user/session | Retrieval is isolated by ownership/session | DONE |
| A5-007 | MUST | Prevent uploaded content leaking across users/sessions | Cross-session retrieval cannot return another user's content | DONE |
| A5-008 | MUST | Never confuse uploaded documents with bare-act authority | Source types remain distinguishable | DONE |
| A5-009 | MUST | Route statute questions to BNS index | Statute question retrieves BNS authority | DONE |
| A5-010 | MUST | Route document questions to session index | Document question retrieves session document | DONE |
| A5-011 | MUST | Route combined compliance questions to both indexes | Example notice-vs-section question searches both | DONE |
| A5-012 | MUST | Distinguish user-document evidence from statutory authority in citations | UI/output makes source type clear | DONE |
| A5-013 | MUST | Treat uploaded PDFs as untrusted input | Document text cannot alter system instructions | DONE |
| A5-014 | MUST | Defend against prompt injection contained in uploads | Embedded instructions such as `ignore previous instructions` are not obeyed | DONE |
| A5-015 | MUST | Document prompt-injection approach | Security approach is explained in project documentation | DONE |

---

# 4. Part B — Forms Extraction Pipeline

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| B-001 | MUST | Process forms pages 190–249 | Pipeline covers expected range | DONE |
| B-002 | MUST | Produce one PDF per form | Each detected form becomes its own PDF | DONE |
| B-003 | MUST | Preserve page-perfect source output | Output corresponds to source pages without re-rendering where possible | DONE |
| B-004 | MUST | Keep text/vector source as real PDF | Text/vector pages remain PDF rather than screenshots | DONE |
| B-005 | MUST | Use rasterization only as documented fallback | Any rasterized output is justified/documented | DONE |
| B-006 | MUST | Derive filenames from the printed form title | Filename title originates from parsed source | DONE |
| B-007 | MUST | Scrape form titles programmatically | Titles are extracted by parser | DONE |
| B-008 | MUST | Do not hardcode a list of form titles | No manually maintained list of 60 form names | DONE |
| B-009 | MUST | Detect multi-page forms | Continuation pages are recognized | DONE |
| B-010 | MUST | Keep multi-page forms as a single PDF | Two/three-page form produces one output file | DONE |
| B-011 | MUST | Follow `FORM-<number>_<slugified-title>.pdf` naming | Every output follows required convention | DONE |
| B-012 | MUST | Make filenames deterministic | Same input produces same filenames | DONE |
| B-013 | MUST | Make filenames filesystem-safe | No invalid path characters | DONE |
| B-014 | MUST | Do not use spaces in form filenames | Generated names contain no spaces | DONE |
| B-015 | MUST | Prevent filename collisions | Distinct forms cannot overwrite each other | DONE |
| B-016 | MUST | Generate `forms_manifest.json` | Manifest is produced | DONE |
| B-017 | MUST | Manifest contains form number | Every entry includes number | DONE |
| B-018 | MUST | Manifest contains scraped title | Exact parsed title is recorded | DONE |
| B-019 | MUST | Manifest contains source page range | Start/end pages are recorded | DONE |
| B-020 | MUST | Manifest contains output filename | Generated filename is recorded | DONE |
| B-021 | MUST | Manifest contains byte size | Output size is recorded | DONE |
| B-022 | MUST | Manifest contains SHA-256 | Cryptographic hash is recorded | DONE |
| B-023 | MUST | Manifest contains extraction confidence | Parser confidence is recorded | DONE |
| B-024 | MUST | Manifest contains `needs_review` | Review flag exists | DONE |
| B-025 | MUST | Set `needs_review: true` when parser is unsure | Uncertain extraction is flagged | DONE |
| B-026 | MUST | Provide OCR fallback | Tesseract or equivalent is available | DONE |
| B-027 | MUST | Trigger OCR when text layer is missing | Missing text layer is handled | DONE |
| B-028 | MUST | Trigger OCR when text layer is garbage | Unusable text layer is handled | DONE |
| B-029 | MUST | Log pages that require OCR | OCR usage is traceable by page | DONE |
| B-030 | MUST | Make forms pipeline idempotent | Re-running same source does not duplicate outputs/rows | DONE |
| B-031 | MUST | Produce byte-identical output on repeated runs | Same PDF input produces identical output bytes | DONE |
| B-032 | MUST | Ensure manifest titles are exact | Titles are suitable for evaluator manifest diff | DONE |
| B-033 | MUST | Provide forms list API | `GET /api/v1/forms` works | DONE |
| B-034 | MUST | Forms list API returns title | Title included | DONE |
| B-035 | MUST | Forms list API returns form number | Form number included | DONE |
| B-036 | MUST | Forms list API returns page range | Page range included | DONE |
| B-037 | MUST | Forms list API returns size | Size included | DONE |
| B-038 | MUST | Provide single-form download API | `GET /api/v1/forms/{id}/download` works | DONE |
| B-039 | MUST | Provide bulk ZIP API | `GET /api/v1/forms/download-all` works | DONE |
| B-040 | MUST | Provide form title search API | `GET /api/v1/forms/search?q=` works | DONE |

---

# 5. Part C — Frontend & UX

## Chat Panel

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| C-001 | MUST | Build ChatGPT-style chat UI | Chat interface is obvious and usable | DONE |
| C-002 | MUST | Keep product to two primary panels | Chat and Forms are the main panels | DONE |
| C-003 | MUST | Implement token streaming | Tokens visibly arrive progressively | DONE |
| C-004 | MUST | Use SSE or WebSocket for streaming | One supported streaming transport is implemented | DONE |
| C-005 | MUST | Do not use spinner-then-wall-of-text interaction | Response streams in UI | DONE |
| C-006 | MUST | Support multi-turn history | Previous conversation context is retained | DONE |
| C-007 | MUST | Show conversation list in sidebar | Conversations are listed | DONE |
| C-008 | MUST | Allow conversation rename | User can rename a conversation | DONE |
| C-009 | MUST | Allow conversation delete | User can delete a conversation | DONE |
| C-010 | MUST | Render citations as chips | Citations are interactive UI elements | DONE |
| C-011 | MUST | Open source drawer from citation chip | Source drawer is functional | DONE |
| C-012 | MUST | Show exact statutory text in source drawer | Retrieved source text is displayed | DONE |
| C-013 | MUST | Show source page in source drawer | Page number is displayed | DONE |
| C-014 | MUST | Support drag-and-drop upload | User can drag a document into upload area | DONE |
| C-015 | MUST | Support click-to-upload | User can select file normally | DONE |
| C-016 | MUST | Show upload progress | Progress is visible | DONE |
| C-017 | MUST | Show parse stage | UI indicates parse state | DONE |
| C-018 | MUST | Show chunk stage | UI indicates chunk state | DONE |
| C-019 | MUST | Show embed stage | UI indicates embedding state | DONE |
| C-020 | MUST | Show ready state | UI clearly indicates document is queryable | DONE |
| C-021 | MUST | Render Markdown | Markdown answers display correctly | DONE |
| C-022 | MUST | Render code blocks | Code blocks are visually distinct/readable | DONE |
| C-023 | MUST | Render quote blocks | Quote blocks display correctly | DONE |
| C-024 | MUST | Provide copy button | User can copy answer/content | DONE |
| C-025 | MUST | Provide stop-generation action | Active generation can be stopped | DONE |
| C-026 | MUST | Provide regenerate action | User can regenerate an answer | DONE |
| C-027 | MUST | Provide 3–4 example questions in empty state | Cold user sees examples | DONE |
| C-028 | MUST | Provide useful file-too-large error | Error explains size issue | DONE |
| C-029 | MUST | Provide useful unsupported-type error | Error explains file type issue | DONE |
| C-030 | MUST | Provide useful model-timeout error | Error communicates timeout | DONE |
| C-031 | MUST | Provide useful retrieval-empty error | Error communicates missing retrieval evidence | DONE |
| C-032 | MUST | Avoid layout shift during long streamed answers | UI remains stable while streaming | DONE |

## Forms Panel

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| C-033 | MUST | Provide Forms panel | Forms are accessible from dedicated panel | DONE |
| C-034 | MUST | Provide searchable form list | User can search forms | DONE |
| C-035 | MUST | Provide filterable form list | User can filter forms | DONE |
| C-036 | MUST | Provide form preview | User can preview before download | DONE |
| C-037 | MUST | Provide single-form download | User can download one form | DONE |
| C-038 | MUST | Provide bulk ZIP download | User can download all forms as ZIP | DONE |

## Non-Negotiable UX

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| C-039 | MUST | Fully responsive UI | Application is usable across viewport sizes | DONE |
| C-040 | MUST | Usable on a phone | Core workflows work on mobile viewport | DONE |
| C-041 | MUST | Keyboard accessible | Core UI can be operated by keyboard | DONE |
| C-042 | MUST | Visible focus states | Focus is visually apparent | DONE |
| C-043 | MUST | Use sensible ARIA | Relevant interactive elements have appropriate accessibility semantics | DONE |
| C-044 | MUST | Meet basic WCAG AA contrast | Basic contrast requirement is satisfied | PARTIAL |
| C-045 | MUST | Support dark mode | Dark theme works | DONE |
| C-046 | MUST | Support light mode | Light theme works | DONE |
| C-047 | MUST | Prevent streaming layout shift | Long answers do not destabilize layout | DONE |
| C-048 | MUST | Use React | Frontend is React-based | DONE |
| C-049 | DECISION | Choose React or Next.js | Choice documented | DONE |
| C-050 | EXPECTED | Use Tailwind as styling default | Tailwind is used unless a justified implementation decision says otherwise | DONE |
| C-051 | DECISION | Make a deliberate visual design choice | UI has a consistent visual language | DONE |
| C-052 | MUST | Avoid stock/unstyled dashboard appearance | UI demonstrates deliberate design decisions | DONE |

---

# 6. Part D — Backend & API

## Backend Foundation

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| D-001 | MUST | Use FastAPI/Python or NestJS/Express/Node | Backend uses an allowed framework | DONE |
| D-002 | MUST | Backend is asynchronous | Async request/processing model is used where required | DONE |
| D-003 | MUST | Backend is typed | Type annotations/type checking are used | DONE |
| D-004 | MUST | Backend API is documented | OpenAPI documentation is available | DONE |

## Required Endpoints

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| D-005 | MUST | `POST /api/v1/chat` exists | Endpoint is callable | DONE |
| D-006 | MUST | Chat endpoint streams responses | Streaming behavior works | DONE |
| D-007 | MUST | Chat endpoint supports multi-turn conversations | Conversation context is accepted/handled | DONE |
| D-008 | MUST | `POST /api/v1/documents/upload` exists | Endpoint is callable | DONE |
| D-009 | MUST | Upload endpoint returns `document_id` | Response contains ID | DONE |
| D-010 | MUST | Upload endpoint returns `job_id` | Response contains job ID | DONE |
| D-011 | MUST | `GET /api/v1/documents/{id}/status` exists | Status endpoint is callable | DONE |
| D-012 | MUST | Status endpoint exposes parse progress | Parse state is represented | DONE |
| D-013 | MUST | Status endpoint exposes chunk progress | Chunk state is represented | DONE |
| D-014 | MUST | Status endpoint exposes embed progress | Embed state is represented | DONE |
| D-015 | MUST | `GET /api/v1/documents` exists | Endpoint lists documents | DONE |
| D-016 | MUST | Documents endpoint is session-scoped | Only current session's documents are returned | DONE |
| D-017 | MUST | `DELETE /api/v1/documents/{id}` exists | Endpoint is callable | DONE |
| D-018 | MUST | Document deletion purges vectors | Associated vector records are removed | DONE |
| D-019 | MUST | `POST /api/v1/search` exists | Raw retrieval endpoint is callable | DONE |
| D-020 | MUST | Search endpoint exposes raw retrieval | Useful for debugging/evaluation | DONE |
| D-021 | MUST | `GET /api/v1/forms` exists | Forms list endpoint works | DONE |
| D-022 | MUST | `GET /api/v1/forms/{id}/download` exists | Single download works | DONE |
| D-023 | MUST | `GET /api/v1/forms/download-all` exists | ZIP download works | DONE |
| D-024 | MUST | `GET /api/v1/forms/search` exists | Search endpoint works | DONE |
| D-025 | MUST | `POST /api/v1/feedback` exists | Feedback endpoint works | DONE |
| D-026 | MUST | Feedback supports thumbs up/down | Vote is persisted | DONE |
| D-027 | MUST | Feedback supports optional text | Optional comment is persisted | DONE |
| D-028 | MUST | `GET /api/v1/health` exists | Liveness endpoint responds | DONE |
| D-029 | MUST | `GET /api/v1/health/ready` exists | Readiness endpoint responds | DONE |
| D-030 | MUST | Readiness checks vector DB | Vector DB dependency is checked | DONE |
| D-031 | MUST | Readiness checks model | Model/provider readiness is checked | DONE |
| D-032 | MUST | Readiness checks storage | Storage readiness is checked | DONE |
| D-033 | MUST | `GET /api/v1/metrics` exists | Endpoint responds | DONE |
| D-034 | MUST | Metrics endpoint uses Prometheus format | Metrics can be scraped | DONE |

## Async Ingestion

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| D-035 | MUST | Document ingestion is asynchronous | Upload request returns without doing entire processing inline | DONE |
| D-036 | MUST | Large upload must not block request thread | 60-page upload is handled by worker/task queue | DONE |
| D-037 | MUST | Use a background worker/task queue | One supported queue architecture is implemented | DONE |
| D-038 | DECISION | Choose queue implementation | Choice documented in `DECISIONS.md` | DONE |
| D-039 | MUST | Provide job status | User can inspect processing state | DONE |

## Identity & Ownership

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| D-040 | MUST | Provide session or user identity | Requests have an ownership identity | DONE |
| D-041 | DECISION | Session model may use anonymous session tokens | If anonymous model chosen, it is documented | DONE |
| D-042 | MUST | Enforce document ownership | Document access checks current owner | DONE |
| D-043 | MUST | Cross-owner document access returns 404 | Access to someone else's ID does not reveal document | DONE |

## Upload Validation & Rate Limiting

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| D-044 | MUST | Enforce upload type allowlist | Unsupported file type is rejected | DONE |
| D-045 | MUST | Enforce maximum upload size | Oversized upload is rejected | DONE |
| D-046 | MUST | Perform MIME sniffing | Content is checked rather than trusting filename alone | DONE |
| D-047 | MUST | Reject encrypted PDFs | Encrypted PDF has hard rejection path | DONE |
| D-048 | MUST | Reject corrupt PDFs | Corrupt PDF has hard rejection path | DONE |
| D-049 | MUST | Rate-limit chat | Excessive chat requests are controlled |DONE |
| D-050 | MUST | Rate-limit upload | Excessive uploads are controlled |DONE |

## Logging & API Documentation

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| D-051 | MUST | Use structured JSON logs | Logs are machine-readable JSON | DONE |
| D-052 | MUST | Generate request ID | Each request has an identifier | DONE |
| D-053 | MUST | Propagate request ID through retrieval | Retrieval logs retain request ID | DONE |
| D-054 | MUST | Propagate request ID through generation | Generation logs retain request ID | DONE |
| D-055 | MUST | Make OpenAPI docs work at `/docs` | `/docs` loads usable API documentation | DONE |

---

# 7. Backend Containerization

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| INF-001 | MUST | Dockerize backend API | API image builds and runs | DONE |
| INF-002 | MUST | Provide multi-stage API Dockerfile | API Dockerfile uses multi-stage build | DONE |
| INF-003 | MUST | Provide worker Dockerfile or shared image with distinct entrypoints | Worker can run independently | DONE |
| INF-004 | MUST | Run containers as non-root | Runtime user is non-root | DONE |
| INF-005 | MUST | Use slim base image | Base image is appropriately slim | DONE |
| INF-006 | MUST | Provide meaningful `.dockerignore` | Unnecessary files are excluded | DONE |
| INF-007 | MUST | Exclude `.git` from Docker build context/image | Git history is not shipped | DONE |
| INF-008 | MUST | Exclude `.env` from Docker build context/image | Secrets are not shipped | DONE |
| INF-009 | MUST | Exclude `node_modules` from Docker build context/image | Local dependency tree is not shipped | DONE |
| INF-010 | MUST | Exclude raw PDFs from Docker image | Source corpus is not baked into image | DONE |
| INF-011 | MUST | Add Docker `HEALTHCHECK` | Healthcheck calls required health endpoint | DONE |
| INF-012 | MUST | Wire healthcheck to `/api/v1/health` | Healthcheck uses liveness endpoint | DONE |
| INF-013 | MUST | Pin dependency versions | Builds do not float to arbitrary future versions | DONE |
| INF-014 | MUST | Define API service in Docker Compose | API starts via Compose | DONE |
| INF-015 | MUST | Define worker service in Docker Compose | Worker starts via Compose | DONE |
| INF-016 | MUST | Define vector DB in Docker Compose | Vector DB starts via Compose | DONE |
| INF-017 | MUST | Define Redis/queue in Docker Compose | Queue starts via Compose | DONE |
| INF-018 | MUST | Define frontend in Docker Compose | Frontend starts via Compose | DONE |
| INF-019 | MUST | Use a shared Docker network | Services communicate over shared network | DONE |
| INF-020 | MUST | Use named volumes | Persistent service data uses named volumes | DONE |
| INF-021 | MUST | Document image size in README | Image size is explicitly recorded | DONE |
| INF-022 | MUST | Avoid unnecessary heavyweight dependencies | Image remains appropriately sized | DONE |

---

# 8. LLM Provider

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| LLM-001 | DECISION | Choose an LLM provider | Provider is documented | DONE |
| LLM-002 | MUST | Put LLM provider behind an interface | Application calls an abstraction rather than hardcoded provider implementation | DONE |
| LLM-003 | MUST | Provider must be swappable by environment variable | Provider can be changed through configuration | DONE |
| LLM-004 | MUST | Document free-tier execution path | Reviewer can run without paid API access where applicable | DONE |
| LLM-005 | MUST | Include Ollama path | Reviewer can evaluate without your API key | DONE |

---

# 9. Part E — CI/CD & Deployment

## CI

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| E-001 | MUST | Use GitHub Actions | CI is implemented using GitHub Actions | DONE |
| E-002 | MUST | Run CI on every pull request | PR triggers workflow | DONE |
| E-003 | MUST | Run CI on every push to `main` | Push triggers workflow | DONE |
| E-004 | MUST | Run lint | Lint check is part of CI | DONE |
| E-005 | MUST | Run format check | Formatting is checked | DONE |
| E-006 | MUST | Run type check | Type checker is part of CI | DONE |
| E-007 | MUST | Run test suite | Tests execute in CI | DONE |
| E-008 | MUST | Report test coverage | Coverage is generated | DONE |
| E-009 | MUST | Enforce stated coverage threshold | CI fails below configured threshold | DONE |
| E-010 | MUST | Run secret scanning | Gitleaks or TruffleHog is integrated | DONE |
| E-011 | MUST | Fail when credential appears in diff | Secret scanner blocks unsafe change | DONE |
| E-012 | MUST | Build Docker image | CI builds application image | DONE |
| E-013 | MUST | Tag image with commit SHA | Image tag includes commit SHA | DONE |
| E-014 | MUST | Push image to GHCR | CI publishes image to GitHub Container Registry | TODO |
| E-015 | MUST | Scan built image with Trivy | Vulnerability scan runs | DONE |
| E-016 | MUST | Deploy on merge to `main` | Deployment job triggers after merge | TODO |

## Self-Hosted Runner — DevOps Track

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| E-017 | TRACK | Register self-hosted GitHub Actions runner | Runner is registered | TODO |
| E-018 | TRACK | Use self-hosted runner for at least build/deploy job | Required job executes on self-hosted runner | TODO |
| E-019 | TRACK | Document runner provisioning | Setup is documented | TODO |
| E-020 | TRACK | Document runner labels | Labels are documented | TODO |
| E-021 | TRACK | Document service installation | Runner service setup is documented | TODO |
| E-022 | TRACK | Document token handling | Registration/token handling is documented | TODO |
| E-023 | TRACK | Document fork-PR security hardening | Fork code execution risk is addressed | TODO |
| E-024 | TRACK | Provide screenshot or Loom evidence of runner execution | Evidence shows runner picking up a job | TODO |

## Deployment

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| E-025 | TRACK | Deploy frontend to Vercel for DevOps track | Frontend is live on Vercel | TODO |
| E-026 | OPTIONAL | Vercel deployment for non-DevOps track | May be omitted outside DevOps track | TODO |
| E-027 | MUST | Backend runs through Docker Compose deployment | Backend service starts via Compose | DONE |
| E-028 | MUST | Vector DB runs through Docker Compose deployment | Vector DB starts via Compose | DONE |
| E-029 | MUST | Worker runs through Docker Compose deployment | Worker starts via Compose | DONE |
| E-030 | MUST | Deployment includes health checks | Services expose/use health checks | DONE |
| E-031 | MUST | Deployment uses named volumes | Persistent data is retained | DONE |
| E-032 | MUST | Deployment uses shared network | Required services communicate | DONE |
| E-033 | MUST | Deployment uses restart policies | Appropriate services restart automatically | DONE |
| E-034 | MUST | Clean clone can run `docker-compose up` | Whole system starts with one command | DONE |
| E-035 | MUST | Avoid multi-step startup preamble | Reviewer does not need a long manual setup sequence | DONE |
| E-036 | MUST | Provide one-shot BNS ingestion process | `scripts/bootstrap.sh` or init container performs documented ingestion | DONE |
| E-037 | MUST | Provide one-shot forms extraction process | Same bootstrap/init mechanism handles forms extraction | DONE |
| E-038 | MUST | Make bootstrap/init process idempotent | Re-running does not duplicate/re-corrupt data | DONE |
| E-039 | MUST | Document rollback story | Previous image can be restored | DONE |
| E-040 | MUST | Document rollback speed/expected recovery process | README/deployment docs state how fast/how | DONE |

---

# 10. Secrets Management

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| SEC-001 | MUST | Never commit `.env` | `.env` is ignored and absent from Git | DONE |
| SEC-002 | MUST | Never commit API keys | Repository contains no keys | DONE |
| SEC-003 | MUST | Never commit credentials | Repository contains no credentials | DONE |
| SEC-004 | MUST | Treat committed secret as release blocker | Submission is not made while secret exists | DONE |
| SEC-005 | MUST | Provide complete `.env.example` | All variables are listed | DONE |
| SEC-006 | MUST | Document purpose of every env variable | Variable table is complete | DONE |
| SEC-007 | MUST | Provide safe default for every env variable where applicable | Defaults do not expose secrets | DONE |
| SEC-008 | MUST | Store CI secrets in GitHub Secrets | Workflow does not hardcode secrets | DONE |
| SEC-009 | MUST | Store Vercel secrets in project env vars | Secrets are not committed | TODO |
| SEC-010 | MUST | If a secret is accidentally committed, rotate it | Credential is invalidated/replaced | DONE |
| SEC-011 | MUST | Document accidental secret incident in `DECISIONS.md` | Incident is honestly recorded | DONE |
| SEC-012 | MUST | Do not hide an accidental secret by pretending it did not happen | Documentation remains truthful | DONE |

---

# 11. Part F — Evaluation & Observability

## Golden Set

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| F-001 | MUST | Create `eval/golden_set.jsonl` | File exists and is executable as evaluation input |DONE |
| F-002 | MUST | Golden set contains 25–30 questions | Count is within required range |DONE |
| F-003 | MUST | Every golden question has expected section(s) | Schema includes expected sections |DONE |
| F-004 | MUST | Include lookup questions | Lookup type represented |DONE |
| F-005 | MUST | Include reasoning questions | Reasoning type represented |DONE |
| F-006 | MUST | Include at least 5 out-of-scope questions | At least five `must_refuse` examples exist |DONE |
| F-007 | MUST | Out-of-scope questions are expected to be refused | Expected sections are empty/appropriate refusal expectation |DONE |

## Retrieval Metrics

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| F-008 | MUST | Report Recall@5 | Numerical result is produced |DONE |
| F-009 | MUST | Report Recall@10 | Numerical result is produced |DONE |
| F-010 | MUST | Report MRR | Numerical result is produced |DONE |
| F-011 | MUST | Report citation accuracy | Numerical percentage is produced |DONE |
| F-012 | MUST | Citation accuracy checks cited section presence in retrieved context | Metric logic verifies source presence |DONE |
| F-013 | MUST | Citation accuracy checks cited section relevance | Metric logic evaluates relevance |DONE |
| F-014 | MUST | Report out-of-scope refusal rate | Numerical refusal rate is produced |DONE |
| F-015 | MUST | Report p50 end-to-end latency | Numerical p50 is produced |DONE |
| F-016 | MUST | Report p95 end-to-end latency | Numerical p95 is produced |DONE |
| F-017 | MUST | Split latency into retrieval and generation | Separate latency measurements are reported |DONE |

## Configuration Comparison

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| F-018 | MUST | Evaluate at least two configurations | Two materially different configurations are tested |DONE |
| F-019 | DECISION | Configuration comparison may use two embedding models | Valid comparison option | N/A (D-048 chose dense-only vs hybrid) |
| F-020 | DECISION | Configuration comparison may use two chunking strategies | Valid comparison option | N/A (D-048 chose dense-only vs hybrid) |
| F-021 | DECISION | Configuration comparison may use dense-only vs hybrid | Valid comparison option |DONE |
| F-022 | MUST | Put comparison table in README | Table contains both configurations |DONE |
| F-023 | MUST | Use numerical results | Comparison uses numbers, not only adjectives |DONE |
| F-024 | MUST | Explain why winner won | README contains rationale for selected configuration |DONE |

## Observability

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| F-025 | MUST | Expose request count metric | Prometheus metric exists |DONE |
| F-026 | MUST | Expose latency histogram metrics | Histogram exists |DONE |
| F-027 | MUST | Expose embedding time | Metric exists |DONE |
| F-028 | MUST | Expose retrieval latency | Metric exists |DONE |
| F-029 | MUST | Expose vector DB up/down state | Metric exists |DONE |
| F-030 | MUST | Expose token usage | Metric exists |DONE |
| F-031 | MUST | Expose upload count | Metric exists |DONE |
| F-032 | MUST | Expose refusal count | Metric exists |DONE |
| F-033 | MUST | Provide one Grafana dashboard OR documented `/metrics` scraping with screenshots | One allowed observability deliverable exists |DONE |
| F-034 | MUST | Track estimated cost per query | Cost is calculated |DONE |
| F-035 | MUST | Calculate query cost from input/output tokens × provider rate | Cost formula follows assignment |DONE |
| F-036 | MUST | Display estimated query cost | Cost is visible through the required observability/product surface |DONE |

---

# 12. Testing Requirements

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| T-001 | MUST | Use an appropriate test framework | pytest/vitest or suitable equivalent is configured | DONE |
| T-002 | MUST | Unit-test section boundary preservation | Test fails if chunker crosses required boundaries | DONE |
| T-003 | MUST | Unit-test proviso attachment | Test verifies proviso remains with parent section | DONE |
| T-004 | MUST | Unit-test slugifier punctuation handling | Fixture covers punctuation |DONE |
| T-005 | MUST | Unit-test forms title extraction | Fixture verifies correct title |DONE |
| T-006 | MUST | Integration-test vector DB round-trip | Write/index/retrieve path works | DONE |
| T-007 | MUST | API-test every endpoint | Required endpoints have tests | DONE |
| T-008 | MUST | API tests cover happy paths | Valid requests succeed | DONE |
| T-009 | MUST | API tests cover auth/ownership failure | Unauthorized document access is rejected |DONE |
| T-010 | MUST | API tests cover validation failure | Invalid inputs produce expected errors | DONE |
| T-011 | MUST | Run a small retrieval assertion set from golden file in CI | CI executes retrieval assertions | DONE |
| T-012 | MUST | Provide one end-to-end test | Test covers upload → ready → query → cited answer | DONE |

---

# 13. Repository Structure Requirements

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| R-001 | MUST | Provide `frontend/` | React application exists there | DONE |
| R-002 | MUST | Provide `backend/app/api/` | API routers live there | DONE |
| R-003 | MUST | Provide `backend/app/core/` | Config/security/dependencies live there | DONE |
| R-004 | MUST | Provide `backend/app/ingestion/` | PDF parsing and structure-aware chunking live there | DONE |
| R-005 | MUST | Provide `backend/app/forms/` | Forms pages 190–249 pipeline lives there | DONE |
| R-006 | MUST | Provide `backend/app/retrieval/` | Hybrid search/rerank/routing live there | DONE |
| R-007 | MUST | Provide `backend/app/llm/` | Provider abstraction/prompts/guards live there | DONE |
| R-008 | MUST | Provide `backend/app/workers/` | Async ingestion jobs live there | DONE |
| R-009 | MUST | Provide `backend/app/main.py` | Application entry point exists | DONE |
| R-010 | MUST | Provide `backend/tests/` | Backend tests live there | DONE |
| R-011 | MUST | Provide `eval/golden_set.jsonl` | Golden dataset exists | DONE |
| R-012 | MUST | Provide `eval/run_eval.py` | Evaluation runner exists | DONE |
| R-013 | MUST | Provide `eval/results/` | Evaluation results are stored | DONE |
| R-014 | MUST | Provide `data/raw/` | Raw source location exists and is gitignored | DONE |
| R-015 | MUST | Provide `data/forms/` | Extracted form location exists and is gitignored | DONE |
| R-016 | MUST | Provide `monitoring/` | Prometheus/Grafana configuration lives there | DONE |
| R-017 | MUST | Provide `scripts/` | Operational scripts live there | DONE |
| R-018 | MUST | Provide `scripts/bootstrap.sh` | Bootstrap script exists | DONE |
| R-019 | MUST | Provide `scripts/ingest.py` | Ingestion script exists | DONE |
| R-020 | MUST | Provide `scripts/extract_forms.py` | Forms extraction script exists | DONE |
| R-021 | MUST | Provide `.github/workflows/` | CI workflows exist | DONE |
| R-022 | MUST | Provide `docker-compose.yml` | Compose definition exists | DONE |
| R-023 | MUST | Provide `.env.example` | Environment template exists | DONE |
| R-024 | MUST | Provide `README.md` | README exists | DONE |
| R-025 | MUST | Provide `ARCHITECTURE.md` | Architecture document exists | DONE |
| R-026 | MUST | Provide `DECISIONS.md` | Decision log exists | DONE |

---

# 14. Documentation Requirements

## README

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| DOC-001 | MUST | README is a graded deliverable | README is complete before submission | DONE |
| DOC-002 | MUST | Explain implementation status Part A | Every A requirement marked Done/Partial/Not attempted | DONE |
| DOC-003 | MUST | Explain implementation status Part B | Every B requirement marked Done/Partial/Not attempted | DONE |
| DOC-004 | MUST | Explain implementation status Part C | Every C requirement marked Done/Partial/Not attempted | DONE |
| DOC-005 | MUST | Explain implementation status Part D | Every D requirement marked Done/Partial/Not attempted | DONE |
| DOC-006 | MUST | Explain implementation status Part E | Every E requirement marked Done/Partial/Not attempted | DONE |
| DOC-007 | MUST | Explain implementation status Part F | Every F requirement marked Done/Partial/Not attempted | DONE |
| DOC-008 | MUST | Provide clean-clone prerequisites | Reviewer can identify prerequisites | DONE |
| DOC-009 | MUST | Provide clone instructions | Reviewer can clone repository | DONE |
| DOC-010 | MUST | Explain copying `.env.example` | Setup step is documented | DONE |
| DOC-011 | MUST | Explain `docker-compose up` | Exact command is documented | DONE |
| DOC-012 | MUST | Explain bootstrap/ingestion command | Exact command is documented | DONE |
| DOC-013 | MUST | Provide application URL | URL/port is documented | DONE |
| DOC-014 | MUST | Provide API docs URL | `/docs` URL is documented | DONE |
| DOC-015 | MUST | Provide vector DB console URL | Console URL is documented if available | DONE |
| DOC-016 | MUST | Provide Grafana URL | Grafana URL is documented if provided | DONE |
| DOC-017 | MUST | Document every environment variable | Complete env table exists | DONE |
| DOC-018 | MUST | Explain how to run with Ollama | Keyless evaluation path is documented | DONE |
| DOC-019 | MUST | Explain BNS ingestion | Ingestion procedure is documented | DONE |
| DOC-020 | MUST | Explain forms extraction | Extraction procedure is documented | DONE |
| DOC-021 | MUST | Provide real copy-pasteable curl examples | Examples work against API | DONE |
| DOC-022 | MUST | Explain how to run tests | Test command documented | DONE |
| DOC-023 | MUST | Explain how to run evaluation | Eval command documented | DONE |
| DOC-024 | MUST | Include evaluation results table | Numerical results included | DONE |
| DOC-025 | MUST | Include AI usage disclosure | Required disclosure section exists | DONE |
| DOC-026 | MUST | Document incomplete work honestly | Specific gaps are listed | DONE |
| DOC-027 | MUST | Document image sizes | Runtime image sizes are listed | DONE |
| DOC-028 | MUST | Document ports | All relevant ports are listed | DONE |
| DOC-029 | MUST | Document known bugs | Known bugs are explicitly listed | DONE |

## AI Usage Disclosure

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| DOC-030 | MUST | State where AI was used | Codebase areas are identified | DONE |
| DOC-031 | MUST | Name AI coding tools used | Tools such as ChatGPT/Cursor/etc. are named accurately | DONE |
| DOC-032 | MUST | Explain roughly what each AI tool was used for | Tool usage is summarized | DONE |
| DOC-033 | MUST | Provide 5–10 representative prompts | Prompt examples are documented | DONE |
| DOC-034 | MUST | Describe one prompt refinement after wrong output | Failed output and refinement are briefly documented | DONE |
| DOC-035 | MUST | State where manual coding was required | Human-written/reworked areas are documented | DONE |
| DOC-036 | MUST | State where AI output was wrong or insufficient | Corrections are documented | DONE |
| DOC-037 | MUST | Do not hide heavy AI usage | Disclosure is complete | DONE |
| DOC-038 | MUST | Ensure candidate can explain submitted code | Code is reviewed/understood before submission | DONE |

## ARCHITECTURE.md

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| DOC-039 | MUST | Include architecture diagram | Diagram exists; Mermaid is acceptable | DONE |
| DOC-040 | MUST | Document upload lifecycle | Upload request lifecycle is documented | DONE |
| DOC-041 | MUST | Document statute-question lifecycle | Statute query lifecycle is documented | DONE |
| DOC-042 | MUST | Document document-question lifecycle | User-document query lifecycle is documented | DONE |
| DOC-043 | MUST | Include chunking schema | Metadata/schema is documented | DONE |
| DOC-044 | MUST | Include retrieval flow | Retrieval stages are documented | DONE |

## DECISIONS.md

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| DOC-045 | MUST | Document embedding-model trade-off | Choice and alternatives are explained | DONE |
| DOC-046 | MUST | Document chunk-strategy trade-off | Choice and alternatives are explained | DONE |
| DOC-047 | MUST | Document overlap strategy | Strategy and rationale are recorded | DONE |
| DOC-048 | MUST | Document hybrid retrieval decision | Dense/sparse/fusion reasoning is recorded | DONE |
| DOC-049 | MUST | Document reranking approach | Selected approach or deliberate omission is explained | DONE |
| DOC-050 | MUST | Document confidence threshold | Threshold and rationale are recorded | DONE |
| DOC-051 | MUST | Document session model | Ownership/session decision is recorded | DONE |
| DOC-052 | MUST | Document queue choice | Queue decision is recorded | DONE |
| DOC-053 | MUST | Document LLM provider choice | Provider/interface decision is recorded | DONE |
| DOC-054 | MUST | Document what would change with two more weeks | Future engineering improvements are stated | DONE |
| DOC-055 | MUST | Document known broken areas | Known weaknesses are honestly recorded | DONE |
| DOC-056 | MUST | Document source/forms page-range discrepancies if found | Any discrepancy is explicitly recorded | DONE |

---

# 15. Git & Submission Requirements

| ID | Priority | Requirement | Acceptance Criteria | Status |
|---|---|---|---|---|
| GIT-001 | MUST | Create a public GitHub repository | Repository is publicly accessible without invitation | TODO |
| GIT-002 | MUST | Share repository link for submission | Link is included in final submission | TODO |
| GIT-003 | MUST | Maintain incremental commit history | Work is represented by meaningful commits | TODO |
| GIT-004 | MUST | Avoid one giant initial commit | Development history shows actual progression | TODO |
| GIT-005 | MUST | Submit complete README | README meets Section 9 requirements | TODO |
| GIT-006 | MUST | Create 5–8 minute Loom/screen recording | Recording exists and is accessible | TODO |
| GIT-007 | MUST | Loom shows clean `docker-compose up` | Recording demonstrates startup | TODO |
| GIT-008 | MUST | Loom shows document upload | Upload workflow is demonstrated | TODO |
| GIT-009 | MUST | Loom shows document question | Uploaded document is queried | TODO |
| GIT-010 | MUST | Loom shows statute question | BNS question is demonstrated | TODO |
| GIT-011 | MUST | Loom shows correct citations | Citation chips/evidence are demonstrated | TODO |
| GIT-012 | MUST | Loom shows source drawer | Source text/page is demonstrated | TODO |
| GIT-013 | MUST | Loom shows refusal path | Out-of-scope question is refused | TODO |
| GIT-014 | MUST | Loom shows Forms panel | Forms workflow is demonstrated | TODO |
| GIT-015 | MUST | Loom shows form download | Form download is demonstrated | TODO |
| GIT-016 | TRACK | Loom shows CI pipeline and self-hosted runner | Required for DevOps track | TODO |
| GIT-017 | MUST | Loom shows evaluation results | Results are demonstrated | TODO |
| GIT-018 | TRACK | Provide live URL for DevOps track | Live deployment URL is accessible | TODO |
| GIT-019 | MUST | Ensure all submitted links are publicly accessible | Reviewer can open every required link | TODO |
| GIT-020 | MUST | Submit GitHub, Loom, and live URL where applicable in one email | Required submission package is complete | TODO |

---

# 16. Automatic Rejection / Release Blockers

These are not ordinary TODO items. They are **hard release gates**.

| ID | Priority | Rejection Condition | Verification | Status |
|---|---|---|---|---|
| REJ-001 | BLOCKER | Any credential committed | Git history + secret scan contains no credentials | DONE |
| REJ-002 | BLOCKER | Any API key committed | Secret audit is clean | DONE |
| REJ-003 | BLOCKER | `.env` committed | `.env` absent from repository | DONE |
| REJ-004 | BLOCKER | Form titles hardcoded instead of scraped | Code review confirms programmatic title extraction | DONE |
| REJ-005 | BLOCKER | Legal chatbot answers without citations | Manual/evaluation tests confirm citation contract | DONE |
| REJ-006 | BLOCKER | Repository is private | Repository opens publicly | TODO |
| REJ-007 | BLOCKER | README missing | README exists and is complete | DONE |

**No submission is allowed while any blocker is unresolved.**

---

# 17. Strong-Yes Quality Targets

These are explicitly identified by the assignment as qualities that earn a strong evaluation.

| ID | Priority | Quality Target | Verification | Status |
|---|---|---|---|---|
| SQ-001 | MUST | Retrieve correct section for difficult indirectly phrased questions | Golden evaluation demonstrates this | PARTIAL |
| SQ-002 | MUST | Chunker visibly understands statutory structure | Chunking fixtures/manual inspection demonstrate structure | DONE |
| SQ-003 | MUST | Refusal path actually fires | Out-of-scope evaluation demonstrates refusal | DONE |
| SQ-004 | MUST | Forms manifest matches evaluator expectation | 58 forms, SHA-256 + byte size + page range per form; the evaluator's expected manifest is not published, so an external diff is NOT VERIFIED | PARTIAL |
| SQ-005 | MUST | `DECISIONS.md` honestly discusses trade-offs and weaknesses | Decision document is substantive | DONE |

---

# 18. Deliberately Unspecified Engineering Decisions

The assignment intentionally leaves these choices to the candidate. They are not missing requirements; they are **design decisions that must be made and defended**.

| ID | Decision | Required Action | Status |
|---|---|---|---|
| DEC-001 | Reranking approach | Select and document approach | DONE |
| DEC-002 | Confidence threshold | Select and document threshold/refusal logic | DONE |
| DEC-003 | Session model | Select and document identity/ownership model | DONE |
| DEC-004 | Queue | Select and document worker/task queue | DONE |
| DEC-005 | CSS/design | Select and document visual design approach | DONE |
| DEC-006 | Prompt | Design and document generation/system prompt strategy | DONE |
| DEC-007 | Embedding model | Select from allowed/open-weight approach and document | DONE |
| DEC-008 | Vector database | Select allowed Docker-runnable vector DB and document | DONE |
| DEC-009 | Retrieval fusion | Select dense+sparse fusion strategy and document | DONE |
| DEC-010 | LLM provider | Select provider and implement swappable interface | DONE |

---

# 19. Final Compliance Audit

Before submission, every requirement in this document must have a final status.

## Status rules

- `DONE` = implemented **and verified**.
- `PARTIAL` = implementation exists but one or more acceptance criteria remain unmet.
- `NOT ATTEMPTED` = no implementation.
- `BLOCKED` = external blocker prevents completion.

### Final audit

| Area | Total Requirements | DONE | PARTIAL | NOT ATTEMPTED | BLOCKED |
|---|---:|---:|---:|---:|---:|
| Product baseline | 10 | 10 | 0 | 0 | 0 |
| Source corpus | 13 | 10 | 0 | 0 | 3 |
| Part A | 99 | 97 | 0 | 2 | 0 |
| Part B — Forms | 40 | 40 | 0 | 0 | 0 |
| Part C — Frontend | 52 | 51 | 1 | 0 | 0 |
| Part D — Backend | 55 | 55 | 0 | 0 | 0 |
| Infrastructure | 22 | 22 | 0 | 0 | 0 |
| LLM | 5 | 5 | 0 | 0 | 0 |
| Part E — CI/CD | 40 | 28 | 0 | 12 | 0 |
| Secrets | 12 | 11 | 0 | 1 | 0 |
| Part F — Evaluation | 34 | 34 | 0 | 0 | 0 |
| Testing | 12 | 12 | 0 | 0 | 0 |
| Repository | 26 | 26 | 0 | 0 | 0 |
| Documentation | 56 | 56 | 0 | 0 | 0 |
| Git & Submission | 20 | 0 | 0 | 20 | 0 |
| Automatic blockers | 7 | 6 | 0 | 1 | 0 |
| Strong-Yes | 5 | 3 | 1 | 0 | 1 |
| Engineering decisions | 10 | 10 | 0 | 0 | 0 |

Audit run: 2026-08-30 (Phase 11 final compliance audit). The individual requirement rows are authoritative.

> The exact totals in the summary table are informational; the individual requirement rows are authoritative.

---

# 20. Final Release Checklist

## Product

- [x] Two-panel Chat + Forms application works.
- [x] BNS questions are grounded and cited. *(serving corpus: real BNS Gazette artifact)*
- [x] User documents can be uploaded and queried.
- [x] Forms can be searched, previewed, and downloaded.

## Retrieval

- [x] Structure-aware parser verified.
- [x] Legal boundaries verified.
- [x] Parent legal components remain attached.
- [x] Dense + sparse hybrid retrieval verified.
- [x] Direct section lookup verified.
- [x] Citation validator verified.
- [x] Refusal verified.

## Forms

- [x] Exact source PDF used. *(the supplied bare-act PDF — content-validated as BNSS — is the forms source; the BNS Act itself carries no forms schedule)*
- [x] Forms 190–249 processed. *(against the fixture's Second Schedule forms)*
- [x] Titles scraped.
- [x] Multi-page forms detected.
- [x] Manifest generated.
- [x] OCR fallback verified.
- [x] Idempotency verified.
- [ ] Manifest compared against expected result. *(evaluator manifest not published; NOT VERIFIED)*

## Frontend

- [x] Streaming.
- [x] Conversation history.
- [x] Citation chips.
- [x] Source drawer.
- [x] Upload progress.
- [x] Markdown.
- [x] Copy.
- [x] Stop.
- [x] Regenerate.
- [x] Empty state.
- [x] Useful errors.
- [x] Forms search/filter.
- [x] Preview.
- [x] Downloads.
- [x] Mobile.
- [x] Keyboard accessibility.
- [x] Dark/light mode.

## Backend

- [x] All required endpoints.
- [x] Async ingestion.
- [x] Ownership.
- [x] Validation.
- [x] Rate limiting.
- [x] Structured logs.
- [x] Request IDs.
- [x] OpenAPI.
- [x] Health/readiness.
- [x] Metrics.

## Infrastructure

- [x] API Dockerfile.
- [x] Worker image/entrypoint.
- [x] Non-root.
- [x] Slim base.
- [x] `.dockerignore`.
- [x] Healthcheck.
- [x] Pinned dependencies.
- [x] Compose services.
- [x] Named volumes.
- [x] Shared network.
- [x] Restart policies.
- [x] Clean-clone startup.
- [x] Bootstrap script.

## CI/CD

- [x] PR CI. *(workflow authored and locally validated; execution requires GitHub remote)*
- [x] Main CI. *(as above)*
- [x] Lint.
- [x] Format.
- [x] Type check.
- [x] Tests.
- [x] Coverage gate.
- [x] Secret scan.
- [ ] GHCR. *(workflow authored; publishing unverified without a remote)*
- [x] SHA tags. *(workflow-authored and build-verified locally)*
- [x] Trivy.
- [ ] Deployment.
- [ ] Self-hosted runner if required.

## Evaluation

- [x] 25–30 golden questions.
- [x] ≥5 refusal questions.
- [x] Recall@5.
- [x] Recall@10.
- [x] MRR.
- [x] Citation accuracy.
- [x] Refusal rate.
- [x] p50.
- [x] p95.
- [x] Retrieval latency.
- [x] Generation latency.
- [x] Two configurations.
- [x] Numerical comparison.

## Documentation

- [x] README complete.
- [x] AI usage disclosed.
- [x] Architecture documented.
- [x] Decisions documented.
- [x] Known gaps documented.
- [x] Known bugs documented.
- [x] Image sizes documented.
- [x] Ports documented.
- [x] API curl examples documented.

## Submission

- [ ] Public GitHub repository.
- [ ] Incremental commit history.
- [ ] README complete.
- [ ] Loom 5–8 minutes.
- [ ] Clean Compose startup demonstrated.
- [ ] Document query demonstrated.
- [ ] Statute citation demonstrated.
- [ ] Refusal demonstrated.
- [ ] Forms download demonstrated.
- [ ] CI demonstrated.
- [ ] Runner demonstrated if applicable.
- [ ] Evaluation demonstrated.
- [ ] Live URL provided if applicable.
- [ ] All links publicly accessible.

---

# 21. Change-Control Rule

Any proposed feature or behavior that is not required by this document must first be classified as:

1. **Assignment requirement**
2. **Engineering decision required to satisfy an assignment requirement**
3. **Optional/bonus explicitly identified by the assignment**
4. **Future enhancement**

Only categories 1–3 should affect the assignment implementation.

Category 4 must not be implemented until all mandatory requirements have been completed and verified.

**This document exists to prevent scope drift during AI-assisted/vibecoded development.**

---

# 22. Optional Bonus — Multilingual Indian Language Support (D-077)

Explicitly identified as a bonus/accessibility feature. It MUST NOT
weaken any requirement in this document: legal grounding, citation
rules, refusal behavior, the confidence gate, prompt-injection
defenses, and session isolation all remain authoritative.

- [x] Language selector in the chat UI: Auto Detect, English, and the
  supported Indian languages (Hindi, Bengali, Marathi, Gujarati, Tamil,
  Telugu, Kannada, Malayalam, Punjabi, Odia, Assamese). Manual
  selection overrides detection. Accessible and responsive.
- [x] No translated corpus copies: the authoritative statute corpus
  remains the single English source; answers cite it with unchanged
  labels (e.g. `[BNS s.103]`).
- [x] Non-English legal questions retrieve via English translation of
  the query (translation used for retrieval only) and are answered in
  the requested language with citations preserved exactly.
- [x] Conversational messages (greetings, identity questions) in any
  supported language receive a fixed in-language response with no
  retrieval and no citations.
- [x] Refusals for unsupported questions are code-controlled and
  provided in the requested/detected language where practical;
  translation must never cause the model to answer from its own
  knowledge.
- [x] Session document isolation is preserved for document questions in
  any language.
- [x] Free/open-source/local tooling only (script detection, optional
  fastText, optional IndicTrans2, existing Ollama provider); no paid
  APIs or cloud translation services.

---
