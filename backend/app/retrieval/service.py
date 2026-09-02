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
import re
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

# Semantic-relevance confidence gate (remediation of the RRF-overlap-only
# confidence signal). Measured on the BNS corpus with BAAI/bge-base-en-v1.5:
# on-target indirect questions score >= 0.50 top cosine ("Someone stole my
# scooter. What does BNS say?" 0.504 — the hardest observed in-scope case;
# typical conduct questions 0.6-0.72), while clearly out-of-scope or
# non-legal questions score <= 0.47 ("What is the rate of GST on restaurant
# bills?" 0.469; "capital of France" 0.347; injection payloads ~0.49* — see
# below). The floor/saturation band maps top cosine onto a [0, 1] relevance
# factor multiplied into the RRF confidence: high confidence then requires
# BOTH list overlap AND semantic relevance. The threshold (0.1) is
# unchanged — the signal became honest, not stricter on paper.
#
# The narrow gray zone above the floor (e.g. "What is Newton's second law
# of motion?" 0.547, whose "law/force/motion" vocabulary resembles BNS
# criminal-force text) is NOT resolvable by retrieval signals: it is
# delegated to the generation contract — the system prompt's rule 4 makes
# the model answer "I don't know based on the available source material."
# when the evidence does not contain the answer, which is the assignment's
# intended fail-closed behavior (A4-012) for subtly out-of-scope questions.
RELEVANCE_FLOOR = 0.48
RELEVANCE_SATURATION = 0.60

# Statute-title mentions ("Hindu Marriage Act", "US Constitution", "Fourth
# Amendment"): used to detect questions about a statute other than the
# indexed corpus. Title case is required so ordinary uses of the words
# ("the act of cruelty") never match. The comparison itself is against the
# corpus act metadata (SRC-013: no hardcoded statute assumptions).
_STATUTE_TITLE_RE = re.compile(
    r"\b((?:[A-Z][a-z]+|US|USA|UK|IPC)\s+(?:[A-Z][a-z]+\s+){0,3}"
    r"(?:Act|Code|Constitution|Amendment))\b"
)


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
        # Semantic-relevance gate band. The defaults are calibrated for
        # BAAI/bge-base-en-v1.5 cosine scores (see RELEVANCE_FLOOR). An
        # embedder with a different cosine scale (e.g. the deterministic
        # HashingEmbedder, where an on-target hit scores ~0.3) must NOT
        # reuse them: pass ``relevance_floor=None`` to disable the gate
        # and fall back to the RRF-overlap confidence signal alone.
        relevance_floor: float | None = RELEVANCE_FLOOR,
        relevance_saturation: float | None = RELEVANCE_SATURATION,
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
        self._relevance_floor = relevance_floor
        self._relevance_saturation = relevance_saturation

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

        evidence = self._statute_evidence(query, resolved_route, intent, flt, reasons)
        # Document fallback (ARCHITECTURE §14 remediation): a statute-routed
        # query whose statute evidence is INSUFFICIENT falls through to the
        # session's documents before failing closed. The keyword router
        # cannot enumerate every way a user references their upload ("What
        # is the filing date?", "What did the petitioner seek?"), so the
        # route hint is advisory: when the statute corpus cannot ground the
        # question but the session's own documents can, the documents are
        # the evidence. A session without documents, or document retrieval
        # below its confidence threshold, still refuses honestly — the
        # fallback only rescues document-groundable questions, never
        # substitutes weak statute evidence for a refusal.
        if (
            not evidence.sufficient
            and session_id is not None
            and self._document_retrieval is not None
        ):
            document_evidence = self._document_evidence(
                query, resolved_route, intent, session_id, reasons
            )
            if document_evidence.sufficient:
                document_evidence.route = resolved_route
                document_evidence.reasons.append(
                    "statute evidence insufficient; session documents retrieved"
                )
                return document_evidence
        return evidence

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

    def _foreign_statute(self, query: str) -> str | None:
        """Name of a statute the query asks about that is not the corpus.

        Compares Title-case statute mentions against the indexed acts'
        names (from chunk metadata, SRC-013). A mention that shares a
        content word with the corpus act name ("Bharatiya Nyaya Sanhita
        Act") is in scope; anything else ("Hindu Marriage Act", "US
        Constitution", "Indian Contract Act") is out of scope — the corpus
        cannot ground it, so retrieval must fail closed rather than
        substitute look-alike BNS sections. Document evidence is not
        affected: a user's own upload may legitimately cite other statutes.
        """
        match = _STATUTE_TITLE_RE.search(query)
        if match is None:
            return None
        mentioned = set(re.findall(r"[a-z]+", match.group(1).lower()))
        mentioned -= {"act", "code", "constitution", "amendment"}
        if not mentioned:
            return None
        for act in self._store.act_names():
            corpus_words = set(re.findall(r"[a-z]+", act.lower())) - {"act", "code", "2023"}
            if mentioned & corpus_words:
                return None
        return match.group(1)

    def _relevance_factor(self, query: str, flt: MetadataFilter | None) -> float | None:
        """Semantic relevance in [0, 1], or None when unavailable.

        Unavailable means the dense retriever exposes no similarity signal
        (test doubles, degraded wiring) or the gate is disabled for this
        embedder's cosine scale (``relevance_floor=None``): the confidence
        then falls back to the RRF-overlap signal alone.
        """
        if self._relevance_floor is None or self._relevance_saturation is None:
            return None
        top_similarity = getattr(self._dense, "top_similarity", None)
        if top_similarity is None:
            return None
        raw = top_similarity(query, flt)
        if raw is None:
            return None
        cosine = float(raw)
        if cosine <= self._relevance_floor:
            return 0.0
        if cosine >= self._relevance_saturation:
            return 1.0
        return (cosine - self._relevance_floor) / (
            self._relevance_saturation - self._relevance_floor
        )

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

        foreign = self._foreign_statute(query)
        if foreign is not None:
            # The question is about a statute the corpus cannot ground:
            # fail closed (A4-011) instead of substituting look-alike
            # sections from the indexed act.
            reasons.append(f"query names statute '{foreign}' which is not the indexed corpus")
            evidence = RetrievedEvidence(
                query=query,
                route=resolved_route,
                intent=intent,
                results=[],
                sufficient=False,
                confidence=0.0,
                reasons=reasons,
            )
            self._log(query, evidence)
            return evidence

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
        relevance = self._relevance_factor(query, flt)
        if relevance is not None:
            # RRF overlap alone reports ~1.0 for confidently irrelevant
            # evidence; the semantic factor makes high confidence mean
            # relevance (remediation of the blind confidence signal).
            confidence = confidence * relevance
            reasons.append(f"semantic relevance factor {relevance:.3f} from top dense similarity")
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
