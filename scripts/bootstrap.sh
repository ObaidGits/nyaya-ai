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
  python scripts/ingest.py --spec bns --source "$STATUTE_SOURCE"
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
