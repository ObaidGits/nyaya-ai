"""Ingestion pipeline orchestration (REQUIREMENTS SRC-*, A1-*).

Flow::

    PDF -> extraction -> cleaning -> source validation ->
    structure parsing -> structure validation ->
    structure-aware chunking -> metadata ->
    (optional) embeddings -> index storage

Design goals (see the Phase 2 source-replacement requirements):

* **Source-driven**: the corpus expectation lives in a ``CorpusSpec``
  passed in (or loaded from configuration); nothing in the pipeline is tied
  to a specific PDF file or statute identity.
* **Validated**: the detected act title is checked against the spec before
  anything is treated as authoritative. A replacement BNS PDF works by
  pointing at it and re-running — no application-code change.
* **Reproducible**: same source + same ``ingested_at`` timestamp produces
  byte-identical chunk output (stable chunk ids, deterministic ordering).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.ingestion.chunker import StructureAwareChunker
from app.ingestion.embeddings import EmbeddingProvider
from app.ingestion.extract import PageExtractor
from app.ingestion.index_store import ChunkIndex
from app.ingestion.models import (
    Chunk,
    CorpusSpec,
    IngestionResult,
    SourceIdentity,
    ValidationResult,
)
from app.ingestion.parser import StructureParser
from app.ingestion.validation import (
    SourceValidationError,
    validate_source,
    validate_structure,
)

logger = logging.getLogger(__name__)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class IngestionPipeline:
    """Orchestrates the full ingestion flow for one source document."""

    def __init__(
        self,
        spec: CorpusSpec,
        extractor: PageExtractor,
        index: ChunkIndex,
        *,
        embedder: EmbeddingProvider | None = None,
        max_chars: int = 4000,
    ) -> None:
        self.spec = spec
        self.extractor = extractor
        self.index = index
        self.embedder = embedder
        self.chunker = StructureAwareChunker(max_chars=max_chars)

    def run(self, source_path: Path, ingested_at: str) -> IngestionResult:
        """Run ingestion; raises SourceValidationError on corpus mismatch."""
        if not source_path.exists():
            raise SourceValidationError(f"source file not found: {source_path}")

        sha = sha256_file(source_path)
        pages = self.extractor.extract(str(source_path))

        source_validation = validate_source(pages, self.spec, len(pages))
        if not source_validation.ok:
            raise SourceValidationError(
                "source does not match expected corpus: " + "; ".join(source_validation.errors)
            )

        parser = StructureParser(self.spec)
        act = parser.parse(pages)

        structure_validation = validate_structure(act, self.spec)
        if not structure_validation.ok:
            raise SourceValidationError(
                "parsed structure failed validation: " + "; ".join(structure_validation.errors)
            )

        source_uri_base = f"pdf:sha256-{sha[:12]}"
        chunks = self.chunker.chunk(act, source_uri_base, ingested_at)

        vectors: list[list[float]] | None = None
        if self.embedder is not None:
            vectors = self.embedder.embed_texts([c.text for c in chunks])

        written = self.index.upsert(chunks, vectors)

        combined = ValidationResult(
            ok=True,
            act_title_detected=act.act_title_detected,
            errors=[],
            warnings=source_validation.warnings
            + structure_validation.warnings
            + parser.warnings
            + self.chunker.warnings,
        )
        result = IngestionResult(
            source=SourceIdentity(
                filename=source_path.name,
                sha256=sha,
                page_count=len(pages),
                act_title_detected=act.act_title_detected,
            ),
            validation=combined,
            section_count=len(act.sections),
            chunk_count=written,
        )
        logger.info(
            "ingestion complete",
            extra={
                "event": "ingestion_complete",
                "act": act.act,
                "sections": result.section_count,
                "chunks": result.chunk_count,
                "sha256": sha,
            },
        )
        return result

    def dry_run_chunks(self, source_path: Path, ingested_at: str) -> list[Chunk]:
        """Run extraction/parse/chunk only (validation still enforced)."""
        if not source_path.exists():
            raise SourceValidationError(f"source file not found: {source_path}")
        pages = self.extractor.extract(str(source_path))
        source_validation = validate_source(pages, self.spec, len(pages))
        if not source_validation.ok:
            raise SourceValidationError(
                "source does not match expected corpus: " + "; ".join(source_validation.errors)
            )
        act = StructureParser(self.spec).parse(pages)
        structure_validation = validate_structure(act, self.spec)
        if not structure_validation.ok:
            raise SourceValidationError(
                "parsed structure failed validation: " + "; ".join(structure_validation.errors)
            )
        return self.chunker.chunk(act, f"pdf:sha256-{sha256_file(source_path)[:12]}", ingested_at)
