# Nyaya — Final Compliance Matrix (submission readiness, 2026-08-31)

Per-requirement rows live in `docs/REQUIREMENTS.md` (~324 tracked
requirements; that table is authoritative). This file is the final
submission-readiness rollup of the 2026-08-31 repository/submission pass.

## Rollup

| Area | Requirements | Status | Evidence |
|---|---:|---|---|
| Product baseline | 10 | DONE | live stress test, 642 backend + 93 frontend tests |
| Source corpus | 13 | 11 DONE, 2 PARTIAL | SRC-002 (supplied bare-act is BNSS → forms source only; statute uses official BNS Gazette), SQ-004 (evaluator manifest unpublished) |
| Part A — Retrieval & indexing | 99 | 97 DONE, 2 bonus NOT ATTEMPTED | cross-encoder rerank (A3-011), cross-ref resolution (A1-039) are bonus |
| Part B — Forms | 40 | DONE | manifest, 58 forms, OCR fallback, idempotency; APIs live-tested |
| Part C — Frontend | 52 | 51 DONE, 1 PARTIAL | C-044 WCAG AA contrast (basic level met, formal audit not run) |
| Part D — Backend | 55 | DONE | API tests, security tests, live E2E |
| Infrastructure / Docker | 22 | DONE | clean `down -v && up -d --build` verified, all healthy in 423 s incl. rebuild |
| LLM provider system | 5 | DONE | registry abstraction, env-driven config; hosted providers NOT VERIFIED live (no key) |
| Part E — CI/CD | 40 | workflow DONE; GHCR publish + deploy NOT VERIFIED (no GitHub remote) | `.github/workflows/ci.yml`: PR + push-main, lint/format/mypy/pytest≥80% cov, frontend gates, gitleaks full-history, Docker build, Trivy CRITICAL/HIGH fail-closed, SHA-tagged GHCR push on main, gated `deploy` environment |
| Secrets | 12 | 11 DONE, 1 NOT VERIFIED | gitleaks scan needs the remote to run on GitHub |
| Part F — Evaluation & observability | 34 | DONE | golden set (29 q), recall/MRR/citation/refusal, p50/p95, Prometheus |
| Testing | 12 | DONE | unit/integration/API/retrieval/forms/security/E2E/speech/multilingual/frontend |
| Repository | 26 | DONE | structure, .gitignore, no secrets/PDFs/artifacts tracked |
| Documentation | 56 | DONE | README/ARCHITECTURE/DECISIONS/.env.example audited this pass |
| Git & submission | 20 | commits DONE this pass; public repo, push, Loom, demos = MANUAL | see below |
| Automatic blockers | 7 | 6 DONE, 1 (public repo URL) MANUAL | |
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
| CI | WORKFLOW DONE, EXECUTION NOT VERIFIED | no GitHub remote — genuine external blocker |
| Secrets | DONE | none tracked; gitleaks workflow ready; `.env` gitignored |
| Evaluation | DONE | final 2026-08-31 numbers in README |
| Observability | DONE | /metrics, Prometheus, request IDs, cost gauges |
| Documentation | DONE | audited & corrected this pass |
| Public repository | MANUAL | `git remote` not configured; needs owner's GitHub credentials |
| Commit hygiene | DONE this pass | work committed in area-grouped commits; no history rewritten |

## Remaining manual actions (owner)

1. Create the **public** GitHub repository, add it as `origin`, `git push -u origin main`.
2. Observe the first CI run (GHCR publish needs no extra secrets — `GITHUB_TOKEN` suffices).
3. Optional: create the `production` environment (+ deploy secrets) to activate the gated deploy job.
4. Record the Loom demo and add the repository link to the submission form.
