#!/usr/bin/env bash
# One-shot bootstrap: ingest the statute corpus and extract the forms library
# (REQUIREMENTS INF-009/E-027; IMPLEMENTATION_PLAN 10.8).
#
# Idempotent: re-running skips work whose output artifacts already exist.
# Run from the repo root after `docker compose up -d`, either locally
# (./scripts/bootstrap.sh) or via one-off API container:
#   docker compose run --rm api python /app/scripts/bootstrap.py  # not shipped
set -euo pipefail

cd "$(dirname "$0")/.."

# Statute corpus source: the BNS Gazette PDF (content-validated as
# "Bharatiya Nyaya Sanhita, 2023"; DECISIONS.md D-082). The other supplied
# file, BNS_bare_act_2023.pdf, is actually a BNSS gazette — ingestion of it
# is rejected by design (never label BNSS content as BNS).
STATUTE_SOURCE="data/raw/BNS_gazette_2023.pdf"
# Forms source (Part B, pages 190-249): the BNS Act itself carries no forms
# schedule; the assignment's supplied bare-act PDF is the only document with
# a forms section, so the forms library is extracted from it and the
# manifest records its true SHA-256 (see README "Known gaps").
FORMS_SOURCE="data/raw/BNS_bare_act_2023.pdf"
CORPUS_OUT="data/processed/bns_corpus.jsonl"
FORMS_OUT="data/forms"

if [[ ! -f "$STATUTE_SOURCE" ]]; then
  echo "bootstrap: BNS source PDF $STATUTE_SOURCE missing — skipping statute ingestion" >&2
elif [[ -f "$CORPUS_OUT" ]]; then
  echo "bootstrap: $CORPUS_OUT exists — skipping statute ingestion"
else
  echo "bootstrap: ingesting statute corpus"
  python scripts/ingest.py --spec bns --source "$STATUTE_SOURCE" --output "$CORPUS_OUT"
fi

# Populate the Qdrant bns_chunks collection (D-010/D-092) so the API's
# "auto" dense backend can serve statute queries from Qdrant. Best-effort:
# no Qdrant (or unreachable) → the API falls back to the in-process cosine
# index over the JSONL artifact and logs why.
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
if [[ -f "$STATUTE_SOURCE" ]] && curl -fsS "${QDRANT_URL%/}/healthz" >/dev/null 2>&1; then
  if curl -fsS "${QDRANT_URL%/}/collections/bns_chunks" >/dev/null 2>&1; then
    echo "bootstrap: Qdrant collection bns_chunks already populated — skipping upsert"
  else
    echo "bootstrap: upserting corpus vectors to Qdrant at $QDRANT_URL"
    python scripts/ingest.py --spec bns --source "$STATUTE_SOURCE" \
      --output "$CORPUS_OUT" --embed bge --qdrant-url "$QDRANT_URL"
  fi
else
  echo "bootstrap: Qdrant not reachable at $QDRANT_URL — skipping vector upsert (API will use the in-process dense index)" >&2
fi

if [[ -f "$FORMS_SOURCE" ]]; then
  if [[ -f "$FORMS_OUT/forms_manifest.json" ]]; then
    echo "bootstrap: forms manifest exists — skipping forms extraction"
  else
    echo "bootstrap: extracting forms library"
    python scripts/extract_forms.py --source "$FORMS_SOURCE" --output "$FORMS_OUT"
  fi
fi

echo "bootstrap: done"
