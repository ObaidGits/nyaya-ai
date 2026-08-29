"""Embedding seam for the ingestion pipeline (REQUIREMENTS A2-*).

The production embedder is the open-weight model locked in DECISIONS.md
D-011/D-012 (BAAI/bge-base-en-v1.5 via sentence-transformers, batched, with
throughput logging). Heavy imports are lazy so the rest of the ingestion
package — and its tests — never require the model runtime.

A2 model-selection documentation lives in DECISIONS.md; the retrieval
integration itself is Phase 3 work. This module only provides the seam the
ingestion pipeline calls into.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

from app.core.errors import AppError

logger = logging.getLogger(__name__)


class EmbeddingError(AppError):
    """Raised when embedding generation fails."""


class EmbeddingProvider(Protocol):
    """Minimal embedding seam used by the ingestion pipeline."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...

    def dimensions(self) -> int: ...


class BgeEmbedder:
    """Open-weight BAAI/bge-base-en-v1.5 embedder (sentence-transformers).

    Model loads once per process (D-012); embedding is batched and
    throughput is logged (A2-011/A2-012).
    """

    MODEL_NAME = "BAAI/bge-base-en-v1.5"
    QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

    def __init__(self, batch_size: int = 32) -> None:
        self.batch_size = batch_size
        self._model: object | None = None

    def _load(self) -> object:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise EmbeddingError(
                    "sentence-transformers is not installed",
                    code="EMBEDDING_UNAVAILABLE",
                ) from exc
            self._model = SentenceTransformer(self.MODEL_NAME)
        return self._model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        vectors: list[list[float]] = []
        started = time.perf_counter()
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            encoded = model.encode(batch, normalize_embeddings=True)  # type: ignore[attr-defined]
            vectors.extend([list(map(float, vec)) for vec in encoded])
        elapsed = time.perf_counter() - started
        if elapsed > 0:
            logger.info(
                "embedding throughput",
                extra={
                    "event": "embedding_throughput",
                    "model": self.MODEL_NAME,
                    "count": len(texts),
                    "batch_size": self.batch_size,
                    "seconds": round(elapsed, 3),
                    "texts_per_second": round(len(texts) / elapsed, 2),
                },
            )
        return vectors

    def dimensions(self) -> int:
        return 768


class NullEmbedder:
    """Deterministic stub used when embeddings are deferred (e.g. dry runs).

    Emits a fixed-length zero vector per text so pipeline wiring and output
    shapes stay testable without the model runtime. Real corpus indexing
    must NOT use this embedder.
    """

    def __init__(self, dimensions: int = 768) -> None:
        self._dimensions = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._dimensions for _ in texts]

    def dimensions(self) -> int:
        return self._dimensions
