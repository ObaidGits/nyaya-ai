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

import hashlib
import json
import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from app.ingestion.embeddings import BgeEmbedder, EmbeddingProvider
from app.ingestion.models import Chunk
from app.retrieval.models import MetadataFilter

if TYPE_CHECKING:
    from qdrant_client import QdrantClient

logger = logging.getLogger(__name__)

QUERY_PREFIX = BgeEmbedder.QUERY_PREFIX


class DenseRetriever(Protocol):
    """Dense (semantic) retrieval seam."""

    def search(self, query: str, flt: MetadataFilter | None, top_k: int) -> list[str]: ...


def embed_query(embedder: EmbeddingProvider, query: str) -> list[float]:
    """Embed a query with the model-required BGE query prefix (A2-009)."""
    from app.observability.metrics import EMBEDDING_LATENCY

    with EMBEDDING_LATENCY.observe_duration():
        return embedder.embed_texts([f"{QUERY_PREFIX}{query}"])[0]


class QdrantDenseRetriever:
    """Dense retrieval against a Qdrant collection (D-010).

    The collection is populated by the Phase 2 ingestion pipeline
    (``QdrantChunkIndex``); each point payload carries the full chunk
    schema, including ``chunk_id``. The HTTP client is created lazily and
    reused across searches (one TCP pool per API process, not per query);
    ``client`` is an injection seam for tests.
    """

    def __init__(
        self,
        url: str,
        collection: str,
        embedder: EmbeddingProvider,
        *,
        client: QdrantClient | None = None,
    ) -> None:
        self.url = url
        self.collection = collection
        self.embedder = embedder
        self._client = client

    def _qdrant(self) -> QdrantClient:
        if self._client is None:
            from qdrant_client import QdrantClient

            self._client = QdrantClient(url=self.url)
        return self._client

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
        return rest.Filter(must=conditions) if conditions else None  # type: ignore[arg-type]

    def search(self, query: str, flt: MetadataFilter | None, top_k: int) -> list[str]:
        client = self._qdrant()
        vector = embed_query(self.embedder, query)
        response = client.query_points(
            collection_name=self.collection,
            query=vector,
            query_filter=self._filter(flt),  # type: ignore[arg-type]
            limit=top_k,
            with_payload=["chunk_id"],
        )
        return [str((hit.payload or {}).get("chunk_id")) for hit in response.points]

    def top_similarity(self, query: str, flt: MetadataFilter | None) -> float | None:
        """Cosine similarity of the best matching chunk (None when empty).

        Feeds the semantic-relevance confidence gate (remediation of the
        RRF-overlap-only confidence): a Qdrant cosine-space score is already
        the cosine similarity, so it is returned as-is.
        """
        client = self._qdrant()
        vector = embed_query(self.embedder, query)
        response = client.query_points(
            collection_name=self.collection,
            query=vector,
            query_filter=self._filter(flt),  # type: ignore[arg-type]
            limit=1,
            with_payload=False,
        )
        if not response.points:
            return None
        return float(response.points[0].score)


class CosineDenseIndex:
    """In-process cosine dense index (no vector store required).

    Corpus vectors are embedded once (A2-013) and persisted to an optional
    on-disk cache: subsequent startups validate the cache against the
    current corpus (embedder identity + per-chunk text hashes) and reload
    it instead of re-embedding, so an API restart does not pay the full
    corpus embedding cost again. Filters are applied before ranking —
    same D-018 server-side semantics.
    """

    def __init__(
        self,
        chunks: list[Chunk],
        embedder: EmbeddingProvider,
        *,
        vector_cache_path: Path | None = None,
    ) -> None:
        self._chunks = {c.chunk_id: c for c in chunks}
        self._embedder = embedder
        # One-entry query-embedding cache (see _query_vector): a retrieval
        # pass embeds the same query for search and top_similarity.
        self._last_query: str | None = None
        self._last_query_vector: list[float] | None = None
        vectors = (
            self._load_cache(chunks, embedder, vector_cache_path)
            if vector_cache_path is not None
            else None
        )
        if vectors is None:
            vectors = self._embed_and_store(chunks, embedder, vector_cache_path)
        self._vectors = vectors

    # -- vector cache ---------------------------------------------------------

    @staticmethod
    def _embedder_identity(embedder: EmbeddingProvider) -> str:
        model = getattr(embedder, "MODEL_NAME", None) or ""
        return f"{type(embedder).__name__}:{model}"

    @staticmethod
    def _fingerprint(chunks: list[Chunk]) -> dict[str, str]:
        return {c.chunk_id: hashlib.sha256(c.text.encode("utf-8")).hexdigest() for c in chunks}

    def _load_cache(
        self,
        chunks: list[Chunk],
        embedder: EmbeddingProvider,
        path: Path,
    ) -> dict[str, list[float]] | None:
        """Return cached vectors when they match the corpus + embedder exactly."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict) or raw.get("embedder") != self._embedder_identity(embedder):
            return None
        stored = raw.get("vectors")
        fingerprint = self._fingerprint(chunks)
        if not isinstance(stored, dict) or set(stored) != set(fingerprint):
            return None
        stored_hashes = raw.get("hashes")
        if not isinstance(stored_hashes, dict) or any(
            stored_hashes.get(cid) != digest for cid, digest in fingerprint.items()
        ):
            return None
        logger.info("dense vector cache hit", extra={"path": str(path), "chunks": len(chunks)})
        return {cid: list(stored[cid]) for cid in fingerprint}

    def _embed_and_store(
        self,
        chunks: list[Chunk],
        embedder: EmbeddingProvider,
        path: Path | None,
    ) -> dict[str, list[float]]:
        texts = [c.text for c in chunks]
        vectors = embedder.embed_texts(texts)
        result = {c.chunk_id: vec for c, vec in zip(chunks, vectors, strict=True)}
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "embedder": self._embedder_identity(embedder),
                    "hashes": self._fingerprint(chunks),
                    "vectors": result,
                }
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_text(json.dumps(payload), encoding="utf-8")
                tmp.replace(path)
                logger.info("dense vector cache written", extra={"path": str(path)})
            except OSError:
                logger.warning("dense vector cache not writable", extra={"path": str(path)})
        return result

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
        query_vec = self._query_vector(query)
        scored = sorted(
            ((cid, _cosine(query_vec, self._vectors[cid])) for cid in candidates),
            key=lambda pair: (-pair[1], pair[0]),
        )
        return [cid for cid, score in scored[:top_k] if score > 0]

    def top_similarity(self, query: str, flt: MetadataFilter | None) -> float | None:
        """Cosine similarity of the best matching chunk (None when empty).

        Feeds the semantic-relevance confidence gate (remediation of the
        RRF-overlap-only confidence signal). Shares the query embedding with
        a subsequent ``search`` call through a one-entry cache so the hybrid
        path does not embed the same query twice.
        """
        candidates = [cid for cid in self._chunks if self._matches(cid, flt)]
        if not candidates:
            return None
        query_vec = self._query_vector(query)
        return max(_cosine(query_vec, self._vectors[cid]) for cid in candidates)

    def _matches(self, chunk_id: str, flt: MetadataFilter | None) -> bool:
        chunk = self._chunks[chunk_id]
        return flt is None or (
            (flt.act is None or chunk.act == flt.act)
            and (flt.act_short is None or chunk.act_short == flt.act_short)
            and (flt.chapter is None or chunk.chapter == flt.chapter)
            and (flt.section_number is None or chunk.section_number == flt.section_number)
        )

    def _query_vector(self, query: str) -> list[float]:
        """Embed ``query`` with a one-entry cache (last query, last vector)."""
        if self._last_query != query or self._last_query_vector is None:
            self._last_query = query
            self._last_query_vector = embed_query(self._embedder, query)
        vector = self._last_query_vector
        assert vector is not None  # set in the branch above
        return vector


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
