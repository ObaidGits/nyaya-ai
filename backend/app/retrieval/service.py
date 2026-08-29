"""Retrieval service (REQUIREMENTS A3-*; ARCHITECTURE §11-§15, §16 rerank deferral).

Independent of the HTTP layer: accepts a query plus routing/filter
information and returns structured evidence (``RetrievedEvidence``).

Pipeline per statute query::

    section intent? ──yes──▶ deterministic lookup (D-017)
          │ no
          ▼
    dense top-k + sparse top-k ─▶ RRF fusion (D-014) ─▶ evidence

Confidence evaluation (ARCHITECTURE §15) is measured and tunable — the
configured threshold is an initial value, not a hidden quality claim.
Cross-encoder reranking is deliberately deferred (D-016).
"""

from __future__ import annotations

import logging

from app.retrieval.dense import DenseRetriever
from app.retrieval.intent import classify_route, detect_section_intent
from app.retrieval.models import (
    MetadataFilter,
    RetrievalRoute,
    RetrievedEvidence,
    ScoredChunk,
)
from app.retrieval.rrf import rrf_fuse
from app.retrieval.sparse import SparseRetriever
from app.retrieval.store import ChunkStore

logger = logging.getLogger(__name__)


class RetrievalService:
    """Query → structured evidence, statute corpus only at this phase."""

    def __init__(
        self,
        store: ChunkStore,
        dense: DenseRetriever,
        sparse: SparseRetriever,
        *,
        dense_top_k: int = 20,
        sparse_top_k: int = 20,
        rrf_k: int = 60,
        confidence_threshold: float = 0.1,
        final_top_k: int = 10,
    ) -> None:
        self._store = store
        self._dense = dense
        self._sparse = sparse
        self._dense_top_k = dense_top_k
        self._sparse_top_k = sparse_top_k
        self._rrf_k = rrf_k
        self._confidence_threshold = confidence_threshold
        self._final_top_k = final_top_k

    def retrieve(
        self,
        query: str,
        flt: MetadataFilter | None = None,
        *,
        route: RetrievalRoute | None = None,
    ) -> RetrievedEvidence:
        """Run the hybrid retrieval pipeline for a query.

        ``route`` overrides intent-based routing when the caller (API
        layer) already knows the route.
        """
        resolved_route = route or classify_route(query)
        intent = detect_section_intent(query)
        reasons: list[str] = []

        if resolved_route == RetrievalRoute.DOCUMENT:
            # Phase 5 delivers document ingestion; until then the route is
            # an explicit, honest stub — never a fake statute answer.
            return RetrievedEvidence(
                query=query,
                route=resolved_route,
                intent=intent,
                results=[],
                sufficient=False,
                confidence=0.0,
                reasons=["document retrieval not available until Phase 5"],
            )

        if intent is not None:
            # Deterministic precedence for exact identifiers (A3-014).
            chunks = self._store.section_lookup(intent.section_number, act_short=intent.act_short)
            if not chunks:
                # The user's act label ("BNS") may not match the indexed
                # corpus act_short; retry without the act restriction so
                # the section number still resolves deterministically.
                chunks = self._store.section_lookup(intent.section_number)
            if flt is not None:
                chunks = [c for c in chunks if self._store.matches(c, flt)]
            lookup_results = [
                ScoredChunk(chunk=chunk, source="lookup", rrf_score=1.0) for chunk in chunks
            ]
            evidence = RetrievedEvidence(
                query=query,
                route=resolved_route,
                intent=intent,
                results=lookup_results,
                sufficient=bool(lookup_results),
                confidence=1.0 if lookup_results else 0.0,
                reasons=reasons,
            )
            if not lookup_results:
                evidence.reasons.append(
                    f"section {intent.section_number} not present in indexed corpus"
                )
            self._log(query, evidence)
            return evidence

        # Hybrid: dense + sparse candidate pools, RRF fusion (D-014/D-015).
        dense_ids = self._dense.search(query, flt, self._dense_top_k)
        sparse_ids = self._sparse.search(query, flt, self._sparse_top_k)
        fused = rrf_fuse(dense_ids, sparse_ids, k=self._rrf_k)[: self._final_top_k]
        dense_rank = {cid: rank for rank, cid in enumerate(dense_ids, start=1)}
        sparse_rank = {cid: rank for rank, cid in enumerate(sparse_ids, start=1)}

        results: list[ScoredChunk] = []
        for chunk_id, score in fused:
            chunk = self._store.get(chunk_id)
            if chunk is None:
                continue
            if not self._store.matches(chunk, flt):
                continue  # filter enforced server-side for any retriever
            if dense_rank.get(chunk_id) and sparse_rank.get(chunk_id):
                source = "hybrid"
            elif dense_rank.get(chunk_id):
                source = "dense"
            else:
                source = "sparse"
            results.append(
                ScoredChunk(
                    chunk=chunk,
                    rrf_score=score,
                    dense_rank=dense_rank.get(chunk_id),
                    sparse_rank=sparse_rank.get(chunk_id),
                    source=source,
                )
            )

        confidence = self._confidence(results)
        reasons.append(
            f"retrieved {len(results)} chunk(s) via "
            f"dense({len(dense_ids)}) + sparse({len(sparse_ids)})"
        )
        sufficient = bool(results) and confidence >= self._confidence_threshold
        if results and not sufficient:
            reasons.append(
                f"retrieval confidence {confidence:.3f} below threshold "
                f"{self._confidence_threshold:.3f}"
            )
        if not results:
            reasons.append("no chunks matched the query in the indexed corpus")

        evidence = RetrievedEvidence(
            query=query,
            route=resolved_route,
            intent=None,
            results=results,
            sufficient=sufficient,
            confidence=confidence,
            reasons=reasons,
        )
        self._log(query, evidence)
        return evidence

    def _confidence(self, results: list[ScoredChunk]) -> float:
        """Normalized RRF confidence: top score / theoretical max.

        The theoretical max is the score of a result ranked first in both
        lists: 2/(k+1). Normalizing puts overlap-confirmed results near
        1.0 — measurable and tunable, per ARCHITECTURE §15.
        """
        if not results:
            return 0.0
        max_score = 2.0 / (self._rrf_k + 1)
        return min(results[0].rrf_score / max_score, 1.0)

    def _log(self, query: str, evidence: RetrievedEvidence) -> None:
        logger.info(
            "retrieval complete",
            extra={
                "event": "retrieval_complete",
                "route": evidence.route.value,
                "results": len(evidence.results),
                "confidence": round(evidence.confidence, 4),
                "sufficient": evidence.sufficient,
                "query_length": len(query),
            },
        )
