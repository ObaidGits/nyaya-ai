# Nyaya — Final Compliance Matrix (submission readiness, 2026-08-31)

Per-requirement rows live in `docs/REQUIREMENTS.md` (~324 tracked
requirements; that table is authoritative). This file is the final
submission-readiness rollup of the 2026-08-31 repository/submission pass.

## Rollup

| Area | Requirements | Status | Evidence |
|---|---:|---|---|
| Product baseline | 10 | DONE | live stress test, 916 backend + 113 frontend tests (local run 2026-09-02; CI re-runs on push) |
| Source corpus | 13 | 11 DONE, 2 PARTIAL | SRC-002 (supplied bare-act is BNSS → forms source only; statute uses official BNS Gazette), SQ-004 (evaluator manifest unpublished) |
| Part A — Retrieval & indexing | 99 | 97 DONE, 2 bonus NOT ATTEMPTED | cross-encoder rerank (A3-011), cross-ref resolution (A1-039) are bonus |
| Part B — Forms | 40 | DONE | manifest, 58 forms, OCR fallback, idempotency; APIs live-tested |
| Part C — Frontend | 52 | 51 DONE, 1 PARTIAL | C-044 WCAG AA contrast (basic level met, formal audit not run) |
| Part D — Backend | 55 | DONE | API tests, security tests, live E2E |
| Infrastructure / Docker | 22 | DONE | clean `down -v && up -d --build` verified, all healthy in 423 s incl. rebuild; fresh GitHub clone bootstrapped + E2E-green (D-084) |
| LLM provider system | 5 | DONE | registry abstraction, env-driven config; hosted providers NOT VERIFIED live (no key) |
| Part E — CI/CD | 40 | DONE — CI GREEN ON GITHUB (run 33390563891) | backend 916 passed locally 2026-09-02 (CI gate: cov ≥80), frontend 113 tests + build, gitleaks 8.24.3 clean, Docker build + Trivy fail-closed (after D-084 CVE remediation), SHA-tagged images live in `ghcr.io/obaidgits/nyaya-ai/*`, gated deploy summary |
| Secrets | 12 | 11 DONE, 1 NOT VERIFIED | gitleaks scan needs the remote to run on GitHub |
| Part F — Evaluation & observability | 34 | DONE | golden set (29 q), recall/MRR/citation/refusal, p50/p95, Prometheus |
| Testing | 12 | DONE | unit/integration/API/retrieval/forms/security/E2E/speech/multilingual/frontend |
| Repository | 26 | DONE | structure, .gitignore, no secrets/PDFs/artifacts tracked |
| Documentation | 56 | DONE | README/ARCHITECTURE/DECISIONS/.env.example audited this pass |
| Git & submission | 20 | DONE — 20 commits pushed to github.com/ObaidGits/nyaya-ai; Loom demo = MANUAL | see below |
| Automatic blockers | 7 | 7 DONE — public repo live, CI green | |
| Strong-Yes extras | 5 | multilingual + speech implemented & live-verified (en/hi) | |
| Engineering decisions | 10 | DONE | DECISIONS.md D-001..D-083 |

## Focus items

| Requirement | Status | Notes |
|---|---|---|
| Exact BNS corpus | DONE | BNS Gazette 2023, SHA-256 `f2e23229…415bb4`, 358 sections, 433 chunks; supplied bare-act PDF is BNSS → forms source only (documented trade-off) |
| Citation contract | DONE | `[BNS s.103(1)(a)]` / `[Document <id> p.<n>]`; layered validation (existence, granularity, relevance, page range); hardened 2026-08-31 (D-083) |
| Hybrid retrieval | DONE | BGE dense + BM25 + RRF; direct section lookup route |
| Forms extraction | DONE | pages 190–249 of the supplied PDF; scraped titles, manifest w/ SHA-256, OCR fallback, idempotent |
| Document isolation | DONE | session-scoped Redis index, filter inside search, cross-session 403/404 (live-tested) |
| Async ingestion | DONE | upload → Redis queue → arq worker → parse → chunk → embed → ready (live-tested) |
| Docker | DONE | clean-start verified; non-root, multi-stage, pinned, healthchecks, named volumes |
| CI | DONE — VERIFIED | green run 33390563891 (2026-08-31); Trivy CVE remediation D-084 (transformers 5.5.4, protobuf 5.29.6, libssl upgrade, frontend alpine 3.24) |
| Secrets | DONE | none tracked; gitleaks workflow ready; `.env` gitignored |
| Evaluation | DONE | final 2026-08-31 numbers in README |
| Observability | DONE | /metrics, Prometheus, request IDs, cost gauges |
| Documentation | DONE | audited & corrected this pass |
| Public repository | DONE | https://github.com/ObaidGits/nyaya-ai — main pushed, CI green (run 33390563891), GHCR images published |
| Commit hygiene | DONE this pass | work committed in area-grouped commits; no history rewritten |

## Remaining manual actions (owner)

1. ~~Create the public GitHub repository, push main~~ — DONE:
   https://github.com/ObaidGits/nyaya-ai (CI green, GHCR published).
2. ~~Observe the first CI run~~ — DONE: run 33390563891 green
   (backend 628 passed / 85% coverage, frontend, gitleaks, Trivy, GHCR).
3. Optional: create the `production` environment (+ deploy secrets) to
   activate the gated deploy job's actual server rollout.
4. Record the Loom demo and add the repository link to the submission form.
5. Optional: if the parler-tts fallback provider is wanted, install
   `parler_tts==0.2.3` at runtime (removed from requirements-speech.txt
   because it pins a vulnerable transformers version — D-084).
