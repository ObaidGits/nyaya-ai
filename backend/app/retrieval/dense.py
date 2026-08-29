"""Dense retrieval (REQUIREMENTS A2-*, A3-003; ARCHITECTURE §10, D-011/D-012).

Two implementations behind the ``DenseRetriever`` protocol:

* ``QdrantDenseRetriever`` — production path against the Qdrant collection
  locked in D-010; metadata filters become Qdrant payload filters (D-018),
  so filtering happens inside the vector store.
* ``CosineDenseIndex`` — dependency-free in-process cosine index used for
  local runs and tests; same contract, same filter semantics.

Queries use the model-required BGE query prefix (A2-009): passages are
embedded raw, queries are prefixed with
``Represent this sentence for searching relevant passages: ``.
"""

from __future__ import annotations

import logging
import math
from typing import Protocol

from app.ingestion.embeddings import BgeEmbedder, EmbeddingProvider
from app.ingestion.models import Chunk
from app.retrieval.models import MetadataFilter

logger = logging.getLogger(__name__)

QUERY_PREFIX = BgeEmbedder.QUERY_PREFIX


class DenseRetriever(Protocol):
    """Dense (semantic) retrieval seam."""

    def search(self, query: str, flt: MetadataFilter | None, top_k: int) -> list[str]: ...


def embed_query(embedder: EmbeddingProvider, query: str) -> list[float]:
    """Embed a query with the model-required BGE query prefix (A2-009)."""
    return embedder.embed_texts([f"{QUERY_PREFIX}{query}"])[0]


class QdrantDenseRetriever:
    """Dense retrieval against a Qdrant collection (D-010).

    The collection is populated by the Phase 2 ingestion pipeline
    (``QdrantChunkIndex``); each point payload carries the full chunk
    schema, including ``chunk_id``.
    """

    def __init__(
        self,
        url: str,
        collection: str,
        embedder: EmbeddingProvider,
    ) -> None:
        self.url = url
        self.collection = collection
        self.embedder = embedder

    def _filter(self, flt: MetadataFilter | None) -> object | None:
        if flt is None:
            return None
        from qdrant_client.http import models as rest

        conditions = []
        if flt.act is not None:
            conditions.append(rest.FieldCondition(key="act", match=rest.MatchValue(value=flt.act)))
        if flt.act_short is not None:
            conditions.append(
                rest.FieldCondition(key="act_short", match=rest.MatchValue(value=flt.act_short))
            )
        if flt.chapter is not None:
            conditions.append(
                rest.FieldCondition(key="chapter", match=rest.MatchValue(value=flt.chapter))
            )
        if flt.section_number is not None:
            conditions.append(
                rest.FieldCondition(
                    key="section_number", match=rest.MatchValue(value=flt.section_number)
                )
            )
        return rest.Filter(must=conditions) if conditions else None

    def search(self, query: str, flt: MetadataFilter | None, top_k: int) -> list[str]:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=self.url)
        vector = embed_query(self.embedder, query)
        response = client.query_points(
            collection_name=self.collection,
            query=vector,
            query_filter=self._filter(flt),  # type: ignore[arg-type]
            limit=top_k,
            with_payload=["chunk_id"],
        )
        return [str((hit.payload or {}).get("chunk_id")) for hit in response.points]


class CosineDenseIndex:
    """In-process cosine dense index (no vector store required).

    Corpus vectors are embedded once at build time (cold start is a
    one-time job, A2-013); queries reuse the loaded embedder. Filters are
    applied before ranking — same D-018 server-side semantics.
    """

    def __init__(self, chunks: list[Chunk], embedder: EmbeddingProvider) -> None:
        self._chunks = {c.chunk_id: c for c in chunks}
        texts = [c.text for c in chunks]
        vectors = embedder.embed_texts(texts)
        self._vectors = {c.chunk_id: vec for c, vec in zip(chunks, vectors, strict=True)}
        self._embedder = embedder

    def search(self, query: str, flt: MetadataFilter | None, top_k: int) -> list[str]:
        if top_k <= 0:
            return []
        candidates = [
            cid
            for cid, chunk in self._chunks.items()
            if flt is None
            or (
                (flt.act is None or chunk.act == flt.act)
                and (flt.act_short is None or chunk.act_short == flt.act_short)
                and (flt.chapter is None or chunk.chapter == flt.chapter)
                and (flt.section_number is None or chunk.section_number == flt.section_number)
            )
        ]
        query_vec = embed_query(self._embedder, query)
        scored = sorted(
            ((cid, _cosine(query_vec, self._vectors[cid])) for cid in candidates),
            key=lambda pair: (-pair[1], pair[0]),
        )
        return [cid for cid, score in scored[:top_k] if score > 0]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
