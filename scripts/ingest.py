#!/usr/bin/env python3
"""Ingestion CLI (REQUIREMENTS R-019, SRC-009).

Runs the Phase 2 ingestion pipeline over a source PDF:

    PDF -> extraction -> cleaning -> structure-aware parsing -> validation
        -> chunking -> metadata -> (optional) embeddings -> index sink

Usage (from repo root):

    python scripts/ingest.py --spec bns --source data/raw/BNS_bare_act_2023.pdf
    python scripts/ingest.py --spec bnss-dev --source data/raw/BNS_bare_act_2023.pdf \
        --output data/processed/bnss_dev_chunks.jsonl

The corpus spec decides what the source must contain; the pipeline rejects a
source whose detected act title does not match the expected corpus (content
validation, never filename). Replacing the source PDF and rerunning requires
NO application-code changes.

A source manifest (sha256, page count, detected act title, section/chunk
counts, ingested_at) is written next to the chunk output for
reproducibility (SRC-009).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.ingestion.embeddings import BgeEmbedder, NullEmbedder
from app.ingestion.extract import PypdfPageExtractor
from app.ingestion.index_store import JsonlChunkSink, QdrantChunkIndex
from app.ingestion.models import CorpusSpec
from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.validation import SourceValidationError

logger = logging.getLogger("nyay.ingest")

SPECS: dict[str, type[CorpusSpec]] = {
    "bns": CorpusSpec.bns,
    "bnss-dev": CorpusSpec.bnss_dev_fixture,
}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--spec",
        choices=sorted(SPECS),
        default="bns",
        help="corpus the source is expected to contain (default: bns)",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=REPO_ROOT / "data" / "raw" / "BNS_bare_act_2023.pdf",
        help="path to the source PDF",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="chunk JSONL output path (default: data/processed/<spec>_chunks.jsonl)",
    )
    parser.add_argument(
        "--embed",
        choices=("bge", "none"),
        default="none",
        help="embedding provider for real index upserts (default: none — dry JSONL sink)",
    )
    parser.add_argument(
        "--qdrant-url",
        default=None,
        help="upsert to a Qdrant instance at this URL instead of the JSONL sink",
    )
    parser.add_argument(
        "--collection",
        default="bns_chunks",
        help="Qdrant collection name (default: bns_chunks)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    spec = SPECS[args.spec]()
    output = (
        args.output or REPO_ROOT / "data" / "processed" / f"{args.spec}_chunks.jsonl"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    if args.qdrant_url:
        if args.embed != "bge":
            print("--qdrant-url requires --embed bge (real vectors)", file=sys.stderr)
            return 2
        index = QdrantChunkIndex(url=args.qdrant_url, collection=args.collection)
    else:
        index = JsonlChunkSink(output)
    embedder = BgeEmbedder() if args.embed == "bge" else NullEmbedder()

    pipeline = IngestionPipeline(
        spec=spec,
        extractor=PypdfPageExtractor(),
        index=index,
        embedder=embedder,
    )
    ingested_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    try:
        result = pipeline.run(args.source, ingested_at)
    except SourceValidationError as exc:
        print(f"SOURCE REJECTED: {exc}", file=sys.stderr)
        return 2

    manifest = {
        "source": result.source.model_dump(),
        "validation": result.validation.model_dump(),
        "section_count": result.section_count,
        "chunk_count": result.chunk_count,
        "ingested_at": ingested_at,
        "spec": args.spec,
        "embedder": args.embed,
        "output": str(output),
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    print(
        f"ingested {result.source.filename}: {result.section_count} sections, "
        f"{result.chunk_count} chunks -> {output}\nmanifest: {manifest_path}"
    )
    if result.validation.warnings:
        for warning in result.validation.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
