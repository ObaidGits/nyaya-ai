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
from typing import TYPE_CHECKING

from app.retrieval.dense import DenseRetriever
from app.retrieval.intent import classify_route, detect_section_intent
from app.retrieval.models import (
    MetadataFilter,
    RetrievalRoute,
    RetrievedEvidence,
    ScoredChunk,
    SectionIntent,
)
from app.retrieval.rrf import rrf_fuse
from app.retrieval.sparse import SparseRetriever
from app.retrieval.store import ChunkStore

if TYPE_CHECKING:
    from app.documents.retrieval import DocumentRetrievalService

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
        document_confidence_threshold: float = 0.05,
        document_retrieval: DocumentRetrievalService | None = None,
    ) -> None:
        self._store = store
        self._dense = dense
        self._sparse = sparse
        self._dense_top_k = dense_top_k
        self._sparse_top_k = sparse_top_k
        self._rrf_k = rrf_k
        self._confidence_threshold = confidence_threshold
        self._final_top_k = final_top_k
        self._document_confidence_threshold = document_confidence_threshold
        self._document_retrieval = document_retrieval

    def retrieve(
        self,
        query: str,
        flt: MetadataFilter | None = None,
        *,
        route: RetrievalRoute | None = None,
        session_id: str | None = None,
    ) -> RetrievedEvidence:
        """Run the retrieval pipeline for a query.

        ``route`` overrides intent-based routing when the caller (API
        layer) already knows the route. ``session_id`` scopes document
        retrieval (§21); without it document routes fail closed.
        """
        from app.observability.metrics import RETRIEVAL_LATENCY

        with RETRIEVAL_LATENCY.observe_duration(route="total"):
            return self._retrieve(query, flt, route=route, session_id=session_id)

    def _retrieve(
        self,
        query: str,
        flt: MetadataFilter | None,
        route: RetrievalRoute | None,
        session_id: str | None,
    ) -> RetrievedEvidence:
        resolved_route = route or classify_route(query)
        intent = detect_section_intent(query)
        reasons: list[str] = []

        if resolved_route == RetrievalRoute.DOCUMENT:
            return self._document_evidence(query, resolved_route, intent, session_id, reasons)

        if resolved_route == RetrievalRoute.COMBINED:
            document_evidence = self._document_evidence(
                query, resolved_route, intent, session_id, reasons
            )
            statute_evidence = self._statute_evidence(query, resolved_route, intent, flt, reasons)
            statute_evidence.document_hits = document_evidence.document_hits
            statute_evidence.sufficient = (
                statute_evidence.sufficient or document_evidence.sufficient
            )
            if document_evidence.sufficient:
                statute_evidence.reasons.append("session document evidence retrieved")
            return statute_evidence

        return self._statute_evidence(query, resolved_route, intent, flt, reasons)

    def _document_evidence(
        self,
        query: str,
        route: RetrievalRoute,
        intent: SectionIntent | None,
        session_id: str | None,
        reasons: list[str],
    ) -> RetrievedEvidence:
        """Session-scoped user-document retrieval (§21, §34).

        Isolation fails closed: no session id or no configured document
        index means no document evidence — never a global search.
        """
        if session_id is None:
            reasons.append("document route requested without a session id")
            return RetrievedEvidence(
                query=query,
                route=route,
                intent=intent,
                results=[],
                sufficient=False,
                confidence=0.0,
                reasons=reasons,
            )
        if self._document_retrieval is None:
            reasons.append("document retrieval is not configured")
            return RetrievedEvidence(
                query=query,
                route=route,
                intent=intent,
                results=[],
                sufficient=False,
                confidence=0.0,
                reasons=reasons,
            )
        evidence = self._document_retrieval.retrieve(session_id, query)
        confidence = evidence.hits[0].score if evidence.hits else 0.0
        sufficient = bool(evidence.hits) and confidence >= self._document_confidence_threshold
        retrieved = RetrievedEvidence(
            query=query,
            route=route,
            intent=intent,
            results=[],
            document_hits=evidence.hits,
            sufficient=sufficient,
            confidence=confidence,
            reasons=reasons,
        )
        if not evidence.hits:
            retrieved.reasons.append("no session document chunks matched")
        elif not sufficient:
            retrieved.reasons.append(
                f"document retrieval confidence {confidence:.3f} below threshold "
                f"{self._document_confidence_threshold:.3f}"
            )
        self._log(query, retrieved)
        return retrieved

    def _statute_evidence(
        self,
        query: str,
        route: RetrievalRoute,
        intent: SectionIntent | None,
        flt: MetadataFilter | None,
        reasons: list[str],
    ) -> RetrievedEvidence:
        resolved_route = route
        if resolved_route == RetrievalRoute.DOCUMENT:  # pragma: no cover - defensive
            resolved_route = RetrievalRoute.STATUTE

        if intent is not None:
            # Deterministic precedence for exact identifiers (A3-014).
            chunks = self._store.section_lookup(intent.section_number, act_short=intent.act_short)
            if not chunks and intent.act_short is not None:
                # The user's act label ("BNS") may not match the indexed
                # corpus act_short. Retrying without the act restriction is
                # only safe while the corpus holds a single act — then the
                # label is an alias, not a different authority. With a
                # multi-act corpus the requested act is genuinely absent,
                # so we refuse rather than substitute the wrong act's text.
                if len(self._store.act_shorts()) <= 1:
                    chunks = self._store.section_lookup(intent.section_number)
                else:
                    reasons.append(f"act {intent.act_short} not present in the indexed corpus")
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
                "intent": evidence.intent.section_number if evidence.intent else None,
                "results": len(evidence.results),
                "document_hits": len(evidence.document_hits),
                "confidence": round(evidence.confidence, 4),
                "sufficient": evidence.sufficient,
                "query_length": len(query),
            },
        )
