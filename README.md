# Nyaya — Legal Assistant over Indian Criminal Law

Nyaya is a ChatGPT-style legal assistant grounded in the Bharatiya Nyaya
Sanhita (BNS), with user-document upload (RAG), a statutory forms library,
inline citations with source evidence, evaluation, and observability — built
with FastAPI + React + Qdrant + Redis, deployable with one `docker compose up`.

> **Corpus status:** the serving statute corpus is the real **Bharatiya
> Nyaya Sanhita, 2023** Gazette PDF (`data/raw/BNS_gazette_2023.pdf`,
> SHA-256 `f2e23229…415bb4`, 138 pages, 358 sections, 433 chunks — content-
> validated at ingestion, `data/processed/bns_corpus.manifest.json`). The
> second supplied file, `data/raw/BNS_bare_act_2023.pdf`, is actually a
> **BNSS** gazette; ingestion rejects it by design and nothing labels BNSS
> content as BNS. Because the BNS Act itself carries no forms schedule, the
> Part B forms library (pages 190–249) is extracted from that supplied
> bare-act PDF — the only document with a forms section — and its manifest
> records the true source SHA-256 honestly.

## Status summary

| Part | Area | Status |
| --- | --- | --- |
| A | Retrieval & indexing (structure-aware chunking, hybrid search, citations) | Done |
| B | Forms extraction pipeline (pages 190–249, manifest, OCR fallback) | Done |
| C | Frontend (chat + forms panels, streaming, citations, dark/light) | Done |
| D | Backend & API (all endpoints, async ingestion, rate limits) | Done |
| E | CI/CD (GitHub Actions, Gitleaks, Trivy, GHCR publish) | Done — CI green on GitHub (run 33390563891), images published to GHCR |
| F | Evaluation & observability (golden set, metrics, cost) | Done |

Requirement-by-requirement status: `docs/REQUIREMENTS.md`. Honest gap list at
the end of this README.

## Quick start (clean clone)

Prerequisites: Docker (with Compose v2), ~6 GB free disk. That's all — no
local Python/Node needed.

```bash
git clone <repository-url> nyaya && cd nyaya
cp .env.example .env          # defaults work out of the box; no secrets needed
docker compose build
docker compose up -d          # api, worker, redis, qdrant, postgres,
                              # ollama (+ one-shot model pull), prometheus,
                              # frontend
```

**Deployment shapes** (selected in `.env`, config-only — no code changes):

| Shape | `.env` | Result |
| --- | --- | --- |
| Keyless local (default) | `COMPOSE_PROFILES=ollama`, `LLM_PROVIDER=ollama` | in-stack Ollama container + auto model pull |
| API-key hosted LLM | `COMPOSE_PROFILES=` (empty), `LLM_PROVIDER=openai`/`gemini`/`grok`/`groq`/`openrouter`/`openai-compatible`, `LLM_API_KEY=…` | no Ollama container created at all; chat uses the hosted provider |

Speech has the same split: `SPEECH_STT_PROVIDER`/`SPEECH_TTS_PROVIDER`
accept `browser` (Web Speech API / speechSynthesis run client-side — zero
server RAM, recommended for small servers), the local models
(`faster-whisper`, `piper`, …, lazy-loaded since `SPEECH_PRELOAD=0`), or an
OpenAI-compatible cloud endpoint. The `/settings` admin console exposes
all of this at runtime, shows detected CPU/RAM, and warns when a local
speech model is likely to exceed available resources.

# one-shot, idempotent data bootstrap (ingestion + forms extraction):
./scripts/bootstrap.sh        # requires a local Python 3.12 venv, see below
```

The bootstrap script needs the backend dependencies on the host
(`python -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt`),
because ingestion runs the same pipeline code the API uses. Everything else
starts with `docker compose up` alone.

### URLs

| Surface | URL |
| --- | --- |
| Frontend (chat + forms) | http://localhost:3000 |
| API base | http://localhost:8000/api/v1 |
| OpenAPI docs | http://localhost:8000/docs |
| Health / readiness | http://localhost:8000/api/v1/health , `/health/ready` |
| Prometheus metrics | http://localhost:8000/api/v1/metrics |
| Prometheus server | http://localhost:9090 (scrapes the API's `/metrics`; 7-day retention) |
| Qdrant (in-stack) | `http://qdrant:6333` (dashboard at `:6333/dashboard`; not published to the host by default — add a `ports:` entry to expose) |
| Redis / PostgreSQL | internal to the compose network |

### Ports

| Port | Service |
| --- | --- |
| 3000 | frontend (nginx → SPA, `/api` reverse proxy) |
| 8000 | backend API |
| 6333 | Qdrant (internal) |
| 6379 | Redis (internal) |
| 5432 | PostgreSQL (internal) |
| 11434 | Ollama (opt-in `ollama` profile, internal) |

## Environment variables

All configuration is environment-driven; no secrets are committed. Copy
`.env.example` to `.env` (gitignored) and adjust:

| Variable | Purpose | Default |
| --- | --- | --- |
| `APP_NAME` / `APP_ENV` / `LOG_LEVEL` | identity, environment, log verbosity | `Nyaya` / `local` / `INFO` |
| `DATABASE_URL` | PostgreSQL DSN | `postgresql+asyncpg://nyaya:nyaya@localhost:5432/nyaya` |
| `QDRANT_URL` | vector DB endpoint | `http://localhost:6333` |
| `QDRANT_BNS_COLLECTION` / `QDRANT_USER_DOCUMENT_COLLECTION` | Qdrant collection names | `bns_chunks` / `user_document_chunks` |
| `REDIS_URL` | Redis DSN (queue + production document store) | `redis://localhost:6379/0` |
| `LLM_PROVIDER` | provider id (ollama default; swappable, LLM-002/003) | `ollama` |
| `LLM_BASE_URL` / `LLM_MODEL` | provider endpoint / model — built-in providers have doc-verified defaults; leave `LLM_BASE_URL` empty for them | `http://localhost:11434` / `llama3.1:8b` |
| `LLM_API_KEY` | hosted-provider key — set ONLY in `.env`, never commit. Bootstrap default: a key saved in the admin console wins over this value (D-090) | unset |
| `LLM_COST_PER_1K_INPUT_TOKENS` / `LLM_COST_PER_1K_OUTPUT_TOKENS` | cost model rates (local Ollama is free) | `0` |
| `EMBEDDING_MODEL` / `EMBEDDING_BATCH_SIZE` | embedding config | `BAAI/bge-base-en-v1.5` / `32` |
| `DENSE_TOP_K` / `SPARSE_TOP_K` | hybrid retrieval pool sizes | `20` / `20` |
| `RETRIEVAL_CORPUS_PATH` | chunked statute artifact; chat fails closed (503) until set | unset |
| `STORAGE_DIR` | uploaded-PDF storage | `./storage` |
| `MAX_UPLOAD_SIZE_MB` / `ALLOWED_UPLOAD_TYPES` | upload limits | `20` / `application/pdf` |
| `RATE_LIMIT_CHAT_PER_MINUTE` / `RATE_LIMIT_UPLOAD_PER_MINUTE` | per-session sliding-window limits | `20` / `5` |
| `DOCUMENTS_BACKEND` | `memory` (dev) or `redis` (production arq worker path) | `memory` |

### Running with Ollama (keyless path)

The default provider is Ollama — reviewers can run everything without any API
key. Either use the compose profile (above) or point at a host install:

```bash
ollama serve & ollama pull qwen2.5:3b
# .env: LLM_PROVIDER=ollama, LLM_BASE_URL=http://localhost:11434, LLM_MODEL=qwen2.5:3b
```

A hosted provider is a config change only (`LLM_PROVIDER`, `LLM_MODEL`,
`LLM_API_KEY` in `.env` — the base URL defaults to the provider's official
endpoint); the provider sits behind the `LLMProvider` interface. The
`/settings` admin console can switch providers at runtime: saves verify the
candidate first (reachable, key accepted, model offered) and a failed
provider never replaces a working one (D-090). The header "Brain active"
badge reflects the backend's classified health probe at
`GET /api/v1/health/llm`, not a superficial settings check.

## Corpus ingestion & forms extraction

```bash
# statute corpus → data/processed/bns_corpus.jsonl (+ SHA-256 manifest)
python scripts/ingest.py --spec bns --source data/raw/BNS_gazette_2023.pdf

# forms (pages 190–249 of the supplied bare-act PDF) → data/forms/*.pdf + forms_manifest.json
python scripts/extract_forms.py --source data/raw/BNS_bare_act_2023.pdf --output data/forms

# both, idempotently (skips steps whose artifacts already exist):
./scripts/bootstrap.sh
```

Ingestion validates the source by **content** (detected act title/structure),
never by filename; a mismatched PDF is rejected. Replacing the source PDF and
re-running re-indexes the corpus with no code changes.

## Copy-pasteable API examples

```bash
# health & readiness
curl -s http://localhost:8000/api/v1/health
curl -s http://localhost:8000/api/v1/health/ready

# chat (SSE stream; session header scopes document retrieval)
curl -N -X POST http://localhost:8000/api/v1/chat \
  -H 'Content-Type: application/json' -H 'X-Session-Id: my-session-0001' \
  -d '{"message":"What is section 103 of the BNS about?"}'

# upload a PDF (async: queued → parsed → chunked → embedded → ready)
curl -X POST http://localhost:8000/api/v1/documents/upload \
  -H 'X-Session-Id: my-session-0001' -F 'file=@./my-notice.pdf'
curl -s http://localhost:8000/api/v1/documents -H 'X-Session-Id: my-session-0001'

# forms
curl -s http://localhost:8000/api/v1/forms
curl -s 'http://localhost:8000/api/v1/forms/search?q=warrant' | head -c 400
curl -s -o form-1.pdf http://localhost:8000/api/v1/forms/1/download
curl -s -o all-forms.zip http://localhost:8000/api/v1/forms/download-all

# feedback
curl -X POST http://localhost:8000/api/v1/feedback \
  -H 'Content-Type: application/json' -H 'X-Session-Id: my-session-0001' \
  -d '{"vote":"up","comment":"good citation"}'

# metrics (Prometheus text)
curl -s http://localhost:8000/api/v1/metrics | head -20
```

## Tests

```bash
# backend (pytest; includes API, retrieval, forms, security, worker tests)
cd backend && .venv/bin/python -m pytest -q

# frontend (vitest + testing-library)
cd frontend && npm ci && npm test

# quality gates (same ones CI runs)
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app
cd frontend && npm run lint && npm run typecheck && npm run build
```

## Evaluation

```bash
python eval/run_eval.py --corpus data/processed/bnss-dev_chunks.jsonl            # deterministic
python eval/run_eval.py --corpus data/processed/bnss-dev_chunks.jsonl --llm      # + Ollama
python eval/run_eval.py --corpus data/processed/bnss-dev_chunks.jsonl --llm --bge  # + BGE + Ollama
```

Results (2026-08-31 remediation run, 29 golden questions, BNSS dev corpus,
live Ollama qwen2.5:3b + BGE on CPU; the golden set was authored against the
BNSS dev fixture, so the eval corpus matches it — the SERVING corpus is the
real BNS Gazette artifact):

| Configuration | Recall@5 | Recall@10 | MRR | Refusal correctness | Citation accuracy | Retrieval p50 |
| ------------- | -------- | --------- | --- | ------------------- | ----------------- | ------------- |
| dense-only (BGE) | 0.310 | 0.345 | 0.315 | 0.862 | 0.621 | 57.7 ms |
| **hybrid (BGE)** | **0.345** | **0.552** | **0.348** | **0.793** | **0.552** | 50.2 ms |

**Why hybrid won:** dense + BM25 sparse + RRF fusion beats dense-only on
Recall@10 (+20.7 pts) and MRR. Lexical precision carries section-identifier
queries. Full analysis in DECISIONS.md. Numbers vary run-to-run with the
live LLM (refusal/citation columns); the retrieval columns are deterministic
per embedder.

**Reading the recall numbers:** the headline Recall@5/@10/MRR average over
ALL 29 golden cases — including 8 refusal-target cases with no
`expected_sections`, which count as 0. Restricted to the 21 retrieval
cases: hybrid(BGE) R@5 0.476 / R@10 0.762 / MRR 0.480; sparse-only(BM25)
R@5 0.857 / R@10 0.905 / MRR 0.723 (the golden set is lexically biased);
dense-only(BGE) R@5 0.429. The metric was deliberately left unchanged for
comparability; no thresholds were tuned to inflate any number
(DECISIONS.md D-083).

## Observability

Prometheus text metrics at `GET /api/v1/metrics`: request counters, latency
histograms (request/embedding/retrieval), vector-DB up gauges, token usage,
upload/refusal counters, estimated query cost
(`nyaya_estimated_query_cost_total`). A ready scrape config ships in
`monitoring/prometheus.yml`. Cost per query = tokens/1000 × the
`LLM_COST_PER_1K_*` rates.

## Deployment

- `docker-compose.yml` — api, worker (shared image, arq entrypoint), redis,
  qdrant, postgres, ollama (opt-in `ollama` profile — on by default via
  `COMPOSE_PROFILES=ollama` in `.env`), prometheus (metrics at
  http://localhost:9090), frontend; named volumes; healthchecks; `restart:
  unless-stopped`.

### Small-server deployment (~2 CPU cores / 4 GB RAM)

Measured on the full stack (24-core host, 16 GB RAM — your numbers on a
smaller server will be at least as high):

| Component | Idle RAM | Loaded / peak |
| --- | --- | --- |
| api (BGE embedder resident, speech lazy) | ~0.5 GB | +~1 GB once whisper loads |
| worker (arq, max_jobs=2) | ~15 MB | ~0.5-1 GB during document embedding |
| ollama + qwen2.5:3b (profile on) | ~0.25 GB | ~1.2 GB after first chat |
| postgres / qdrant / redis / prometheus / frontend | ~95 MB combined | similar |
| **Total, full local shape** | **~0.9 GB** | **~3-3.5 GB** |

Recommended `.env` for 2 CPU / 4 GB:

```bash
COMPOSE_PROFILES=ollama            # keep; full local LLM fits (~1.2 GB after
                                   # first chat). Drop to empty + a hosted
                                   # LLM_API_KEY if you also want whisper.
SPEECH_PRELOAD=0                   # default — speech models load lazily
SPEECH_STT_PROVIDER=browser        # zero server RAM (Web Speech API)
SPEECH_TTS_PROVIDER=browser        # zero server RAM (speechSynthesis)
```

Swap individual pieces as needed: any of `faster-whisper`/`whisper`/
`indicconformer` for STT and `piper` (light, ~200 MB) for TTS still work,
but keep them lazy (`SPEECH_PRELOAD=0`). Corpus dense vectors are cached at
`storage/retrieval_dense_vectors.json` after the first start, so an API
restart no longer re-embeds the BNS corpus. The admin console's System
status panel shows the server's detected cores/RAM and warns before a
local speech model is likely to exceed them.
- Images: `nyaya/backend:local` ≈ 5.4 GB (multi-stage `python:3.12-slim`,
  non-root, pinned deps; the size is dominated by the CPU-only torch stack,
  the baked BGE embedding model and Piper voices — everything runs offline),
  `nyaya/frontend:local` ≈ 74 MB
  (nginx-unprivileged). No secrets baked in; config is env-driven.
- **Rollback:** CI tags images with the commit SHA in GHCR; redeploy the
  previous SHA tag and `docker compose up -d api worker`. Recovery is a
  container restart (<1 min) — state lives in named volumes, not images.
- CI (`.github/workflows/ci.yml`): PR + push-to-main triggers; backend
  lint/format/mypy/pytest with coverage ≥80% (includes the golden-set
  retrieval assertions, `tests/evaluation/test_golden_retrieval.py`);
  frontend lint/typecheck/test/build; Gitleaks secret scan (full history);
  Docker build + Trivy scan (CRITICAL/HIGH, fail-closed); GHCR push with
  commit-SHA tags on main; a gated `deploy` job (GitHub `production`
  environment) publishes a release summary and is the single point to hook
  the server rollout into.
- **Manual deployment step (remaining):** on the deployment server, pull the
  SHA-tagged images and restart —
  `docker compose pull && docker compose up -d` with
  `image: ghcr.io/<owner>/<repo>/nyaya-backend:<sha>` overrides in
  `docker-compose.yml`. Server credentials are not in this repository; the
  rollout itself is NOT claimed as verified.

## AI usage disclosure

This project was implemented **AI-assisted, prompt-driven**, with the
candidate reviewing, testing, and correcting every phase. Honesty about this
is a project requirement (AI_RULES.md), so here is the full picture.

**Where AI was used:** effectively the entire codebase — backend (FastAPI
services, ingestion, retrieval, forms pipeline, workers, tests), frontend
(React components, hooks, tests), Docker/CI/deployment, and documentation —
was generated with **Claude Code** (Anthropic CLI coding agent) driven by
phase-by-phase prompts authored by the candidate.

**Where manual work was required:** requirements decomposition
(`docs/REQUIREMENTS.md`), the phase prompts and their scope discipline, all
verification runs (tests, compose smoke tests, gitleaks scans), and decisions
about what *not* to build. AI output was wrong often enough that correction
was routine — examples below.

**Representative prompts (5 of many, condensed):**

1. "Implement Phase 2 — Ingestion & Chunking. Read the docs. Structure-aware
   chunking, never split mid-sentence, provisos stay attached. Do not start
   Phase 3."
2. "Implement Phase 5 — User Documents. Upload → async ingestion lifecycle →
   session-scoped retrieval. Cross-session access must return 404."
3. "Implement Phase 9 — Testing & Security. Adversarial tests: prompt
   injection, path traversal, oversized uploads, MIME spoofing, SSRF. Do not
   modify tests to make them pass."
4. "Implement Phase 10 — Docker, CI/CD & Deployment. Compose stack with api,
   worker, redis, qdrant; non-root containers; CI with secret scanning and
   Trivy. No secrets baked into images."
5. "Audit the entire project requirement-by-requirement. Do not mark anything
   DONE without evidence."

**One prompt refinement after wrong output:** during Phase 9 the AI wrote a
test asserting that a filename containing a NUL byte (`evil\x00.pdf`) is
rejected by the HTTP upload endpoint. The test failed — not because validation
was missing, but because the multipart transport strips NUL bytes
client-side, so the server never sees them. The wrong conclusion would have
been "add server-side NUL handling to make the test pass." Instead the test
was refined to call `validate_upload()` directly (where the control-character
rejection genuinely lives and is enforced), and the HTTP layer was verified
separately. The lesson — test the boundary that actually owns the invariant —
was applied to later phases.

**Where AI output was wrong or insufficient (corrected):** the worker
container initially connected to `localhost` instead of the compose Redis DSN
(fixed by reading `REDIS_URL` at worker start); nginx cached the API
container's IP at startup and 502'd after API restarts (fixed with Docker's
embedded DNS resolver); the frontend "non-root" image initially still ran the
nginx master as root (switched to `nginxinc/nginx-unprivileged`); the
readiness tests assumed a single configuration check (rewritten when real
dependency checks landed).

## Known gaps & limitations (honest list)

1. **Forms source (documented trade-off):** the BNS Act carries no forms
   schedule; the forms library is extracted from the assignment's supplied
   bare-act PDF (pages 190–249), which is actually a BNSS gazette. The
   manifest records the true source SHA-256; no BNSS text is served as BNS
   statute content.
2. **Refusal correctness is 0.793, not ~1.0:** the 3B local model sometimes
   fails citation formatting and the guard correctly refuses ("no grounded
   sentence survived validation"). The guard never lets an uncited legal
   answer through; the cost is occasional over-refusal.
3. **46 untitled sections:** Gazette marginal notes are interleaved
   mid-sentence by the PDF text layer (e.g. s.282, s.300); the parser
   refuses to guess titles (no manufacturing) and flags them in the
   manifest. A three-iteration pdfplumber layout prototype recovered only
   3/46 with one WRONG title (missing word) under strict validation — the
   layout genuinely prevents reliable extraction (zero positional gap, no
   font discriminator, adjacent-section note concatenation). All 46 stay
   `needs_review`; evidence in DECISIONS.md D-084.
4. **Speech voices:** STT/TTS verified live for English and Hindi only;
   the mr/gu/ta Piper voices synthesize unintelligible audio from Indic
   script — NOT VERIFIED for those languages.
5. **GHCR publish + deploy-on-main (E-014/E-016): VERIFIED.** CI run
   33390563891 is green end-to-end on GitHub: backend (628 passed, 85%
   coverage ≥80), frontend (93 tests + build), gitleaks (pinned 8.24.3
   CLI, clean), Docker build + Trivy fail-closed scans, and
   `ghcr.io/obaidgits/nyaya-ai/nyaya-{backend,frontend}:<sha>` pushed to
   GHCR. The gated `deploy` job records a release summary; the actual
   server rollout needs the `production` environment (manual).
6. **Readiness dependency checks** cover vector DB, model provider, storage
   and Redis reachability, but do not validate model *correctness*.
7. **Feedback persistence is in-process memory** (telemetry only; no account
   model to attach votes to). The store is a single swap-point class.
8. **Combined statute+document answers:** the COMBINED route merges session
   document evidence with statute evidence, both independently validated by
   the citation guard — live-verified (upload → combined question → s.103 +
   document chunks retrieved, cited answer produced; DECISIONS.md D-084).
   qwen2.5:3b sometimes fails the dual citation format and the guard then
   refuses (honest limitation), rather than emitting ungrounded text.
9. **Cross-encoder reranking (A3-011) and query-time cross-reference
   resolution (A1-039) are BONUS items — not attempted.**
10. **DevOps-track items (E-017..E-025: self-hosted runner, Vercel) are
    TRACK-dependent and not attempted** on this track (targeting
    Full Stack).

## Known bugs

None known-open at final-audit time (all 643 backend + 93 frontend tests
pass, stack rebuilt clean and stress-tested live; a fresh clone from
GitHub was bootstrapped and exercised end-to-end — DECISIONS.md D-084).
Historical defects found and fixed during development are listed in
`docs/DECISIONS.md`.

## Repository layout

```
backend/   FastAPI app (api, core, ingestion, forms, retrieval, llm, workers, generation, documents, observability, evaluation)
frontend/  React + Vite + Tailwind (chat + forms panels)
data/      raw source + processed artifacts + extracted forms (gitignored)
docs/      PRD, REQUIREMENTS, ARCHITECTURE, DECISIONS, AI_RULES, IMPLEMENTATION_PLAN
eval/      golden_set.jsonl, run_eval.py, results/
monitoring/prometheus.yml
scripts/   bootstrap.sh, ingest.py, extract_forms.py
```
