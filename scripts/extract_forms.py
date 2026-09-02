#!/usr/bin/env python3
"""Forms extraction CLI (REQUIREMENTS B-001..B-032; DECISIONS D-002).

Runs the Phase 6 forms pipeline over the exact source PDF:

    PDF -> page range (190-249) -> (OCR fallback) -> boundary detection
        -> title scraping -> page-perfect form PDFs -> forms_manifest.json

Usage (from repo root):

    python scripts/extract_forms.py \
        --source data/raw/BNS_bare_act_2023.pdf --output data/forms

The pipeline is content-driven and idempotent: re-running it on the same
source rewrites byte-identical outputs and the same manifest (no duplicate
forms). Detection never relies on a hardcoded list of form titles.

The file currently in ``data/raw/`` is the BNSS development fixture while
the correct BNS source is awaited from DhronAI; the forms it carries are the
Second Schedule forms of that fixture and the manifest records the exact
source filename, SHA-256, and the act title detected from the source text
(the BNSS, per DECISIONS #74 — never the misleading filename) for
traceability.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.forms.pipeline import (
    DEFAULT_FORMS_PAGE_END,
    DEFAULT_FORMS_PAGE_START,
    FormsExtractionError,
    FormsExtractor,
)
from app.forms.models import MANIFEST_FILENAME

logger = logging.getLogger("nyay.forms")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source",
        type=Path,
        default=REPO_ROOT / "data" / "raw" / "BNS_bare_act_2023.pdf",
        help="exact source PDF to extract forms from",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "data" / "forms",
        help="directory that receives the form PDFs and manifest",
    )
    parser.add_argument(
        "--page-start",
        type=int,
        default=DEFAULT_FORMS_PAGE_START,
        help="first source page of the forms range (default: 190)",
    )
    parser.add_argument(
        "--page-end",
        type=int,
        default=DEFAULT_FORMS_PAGE_END,
        help="last source page of the forms range (default: 249)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    extractor = FormsExtractor(page_start=args.page_start, page_end=args.page_end)
    try:
        manifest = extractor.extract(str(args.source), args.output)
    except FormsExtractionError as exc:
        print(f"FORMS EXTRACTION FAILED [{exc.code}]: {exc.message}", file=sys.stderr)
        return 1

    review = sum(1 for form in manifest.forms if form.needs_review)
    print(f"source: {manifest.source.filename} (sha256 {manifest.source.sha256[:12]}...)")
    print(f"act:    {manifest.source.act_title or 'not detected in source text'}")
    print(f"range:  pages {manifest.source.page_start}-{manifest.source.page_end}")
    print(f"forms:  {len(manifest.forms)} extracted, {review} flagged needs_review")
    print(f"output: {args.output / MANIFEST_FILENAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
