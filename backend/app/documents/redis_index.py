"""Redis-backed session-scoped document index (production path, D-030).

Vectors and chunk texts live in Redis so the arq worker (upsert) and the
API process (search) share one index. The session filter is applied inside
the search, never after it (ARCHITECTURE §21).
"""

from __future__ import annotations

import json
import logging
import math

import redis

from app.documents.ingestion import DocumentIndex
from app.ingestion.embeddings import EmbeddingError

logger = logging.getLogger(__name__)

_VECTORS_KEY_PREFIX = "nyaya:docvectors:"  # session -> {chunk_id: [floats]}
_TEXTS_KEY_PREFIX = "nyaya:doctexts:"  # session -> {chunk_id: text}
_DOC_CHUNKS_KEY_PREFIX = "nyaya:docindex:"  # document -> [chunk_ids]


class RedisDocumentIndex(DocumentIndex):
    """Session-scoped cosine index over Redis hashes."""

    def __init__(self, client: redis.Redis) -> None:
        self._redis = client

    def upsert(
        self,
        session_id: str,
        document_id: str,
        chunks: list[tuple[str, str, list[float]]],
    ) -> int:
        vectors_key = f"{_VECTORS_KEY_PREFIX}{session_id}"
        texts_key = f"{_TEXTS_KEY_PREFIX}{session_id}"
        pipeline = self._redis.pipeline()
        for chunk_id, text, vector in chunks:
            pipeline.hset(vectors_key, chunk_id, json.dumps(vector))
            pipeline.hset(texts_key, chunk_id, text)
        pipeline.set(
            f"{_DOC_CHUNKS_KEY_PREFIX}{document_id}",
            json.dumps([chunk_id for chunk_id, _text, _vector in chunks]),
        )
        pipeline.execute()
        return len(chunks)

    def delete(self, session_id: str, document_id: str) -> int:
        doc_chunks: str | None = self._redis.get(  # type: ignore[assignment]
            f"{_DOC_CHUNKS_KEY_PREFIX}{document_id}"
        )
        chunk_ids: list[str] = json.loads(doc_chunks) if doc_chunks else []
        if not chunk_ids:
            return 0
        pipeline = self._redis.pipeline()
        pipeline.hdel(f"{_VECTORS_KEY_PREFIX}{session_id}", *chunk_ids)
        pipeline.hdel(f"{_TEXTS_KEY_PREFIX}{session_id}", *chunk_ids)
        pipeline.delete(f"{_DOC_CHUNKS_KEY_PREFIX}{document_id}")
        pipeline.execute()
        return len(chunk_ids)

    def search(
        self,
        session_id: str,
        query_vector: list[float],
        *,
        top_k: int,
        document_ids: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        vectors: dict[str, str] = self._redis.hgetall(  # type: ignore[assignment]
            f"{_VECTORS_KEY_PREFIX}{session_id}"
        )
        if not vectors:
            return []
        allowed: set[str] | None = None
        if document_ids is not None:
            allowed = set()
            for document_id in document_ids:
                raw: str | None = self._redis.get(  # type: ignore[assignment]
                    f"{_DOC_CHUNKS_KEY_PREFIX}{document_id}"
                )
                if raw:
                    allowed.update(json.loads(raw))
        scored: list[tuple[str, float]] = []
        mismatches = 0
        for chunk_id, raw_vector in vectors.items():
            key = chunk_id.decode() if isinstance(chunk_id, bytes) else chunk_id
            if allowed is not None and key not in allowed:
                continue
            vector = json.loads(raw_vector)
            if len(vector) != len(query_vector):
                # Embedded by a process whose embedder differed from this
                # one (e.g. worker BGE 768-dim vs API hashing fallback
                # 256-dim). Counted and reported below instead of silently
                # scoring 0.
                mismatches += 1
                continue
            score = _cosine(query_vector, vector)
            if score > 0:
                scored.append((key, score))
        if mismatches and not scored:
            # Every stored vector is unusable: retrieval would silently
            # return nothing. Fail loud — the client sees a 503 with a
            # clear cause rather than an empty evidence set.
            raise EmbeddingError(
                "Document index was built with a different embedding model "
                "than the one now in use. Re-upload the documents or restore "
                "EMBEDDING_BACKEND to the original value.",
                status_code=503,
                code="EMBEDDING_MISMATCH",
            )
        if mismatches:
            logger.warning(
                "document index has %d chunk(s) with mismatched embedding dimension",
                extra={"mismatches": mismatches},
            )
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]

    def get_text(self, session_id: str, chunk_id: str) -> str | None:
        raw: str | None = self._redis.hget(  # type: ignore[assignment]
            f"{_TEXTS_KEY_PREFIX}{session_id}", chunk_id
        )
        if raw is None:
            return None
        return raw

    def chunk_ids(self, session_id: str) -> list[str]:
        keys = self._redis.hkeys(f"{_TEXTS_KEY_PREFIX}{session_id}")
        return [key.decode() if isinstance(key, bytes) else key for key in keys]


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
