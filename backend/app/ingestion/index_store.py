"""Chunk index storage seam for the ingestion pipeline (REQUIREMENTS A3-*).

Two implementations:

* ``QdrantChunkIndex`` — production sink into the Qdrant collection locked
  in DECISIONS.md D-010 (``bns_chunks``); lazy import so the package works
  without qdrant-client installed.
* ``JsonlChunkSink`` — deterministic JSONL writer used for the auditable,
  reproducible ingestion artifact (SRC-009) and for tests.

Retrieval against the index is Phase 3; this module only persists chunks
(and vectors when an embedder is supplied).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from app.core.errors import AppError
from app.ingestion.models import Chunk

logger = logging.getLogger(__name__)


class IndexError_(AppError):
    """Raised when writing chunks to the index fails."""


class ChunkIndex(Protocol):
    """Storage seam used by the ingestion pipeline."""

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]] | None) -> int: ...


class JsonlChunkSink:
    """Write chunks as a deterministic JSONL artifact.

    Chunks are written in the given order (the chunker emits deterministic
    order); the file is rewritten atomically on each run so re-ingestion is
    idempotent (no appended duplicates).
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]] | None) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for chunk in chunks:
                handle.write(chunk.model_dump_json() + "\n")
        tmp.replace(self.path)
        return len(chunks)


class QdrantChunkIndex:
    """Upsert chunks (and vectors) into a Qdrant collection.

    Payload metadata keeps the full required chunk schema (AI_RULES §9);
    vectors are stored when an embedder produced them.
    """

    def __init__(self, url: str, collection: str = "bns_chunks") -> None:
        self.url = url
        self.collection = collection

    def _client(self) -> object:
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise IndexError_(
                "qdrant-client is not installed",
                code="INDEX_UNAVAILABLE",
            ) from exc
        return QdrantClient(url=self.url)

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]] | None) -> int:
        if not chunks:
            return 0
        from qdrant_client.http import models as rest

        client = self._client()
        dim = len(vectors[0]) if vectors else 1
        existing = client.collection_exists(self.collection)  # type: ignore[attr-defined]
        if not existing:
            client.create_collection(  # type: ignore[attr-defined]
                collection_name=self.collection,
                vectors_config=rest.VectorParams(size=dim, distance=rest.Distance.COSINE),
            )
        points = [
            rest.PointStruct(
                id=idx + 1,
                vector=(vectors[idx] if vectors else None),
                payload=chunk.model_dump(),
            )
            for idx, chunk in enumerate(chunks)
        ]
        client.upsert(  # type: ignore[attr-defined]
            collection_name=self.collection, points=points
        )
        logger.info(
            "indexed chunks",
            extra={
                "event": "index_upsert",
                "collection": self.collection,
                "count": len(chunks),
            },
        )
        return len(chunks)
