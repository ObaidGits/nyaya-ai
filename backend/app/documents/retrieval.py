"""Session-scoped user-document retrieval (REQUIREMENTS A5-005..A5-012; §34/§35).

Searches only the requesting session's document index (§21: the session
filter is applied inside the index, never as a post-filter over global
results). Results carry document identity so statutory authority and
user-document evidence stay distinguishable (A5-008/A5-012).

Hybrid scoring: dense cosine similarity is fused with a lexical (BM25-style
token overlap) score, mirroring the statute side's dense+sparse hybrid.
Pure dense retrieval misses exact-identifier questions ("Who are the
parties to the suit?" — the parties chunk scores low on embedding
similarity but matches "parties"/"suit" lexically); pure lexical misses
paraphrases. Reciprocal-rank fusion takes the union, ranked by both.
"""

from __future__ import annotations

import math
import re

from app.documents.ingestion import DocumentIndex
from app.documents.models import DocumentEvidence, DocumentHit
from app.documents.references import (
    DocumentReferenceResolution,
    reference_free_query,
    resolve_document_references,
)
from app.documents.store import DocumentStore
from app.domain.models import JobStatus
from app.ingestion.embeddings import EmbeddingProvider
from app.retrieval.dense import embed_query

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_FLOOR_TOKENS = frozenset(
    {"what", "who", "is", "are", "the", "a", "an", "of", "to", "in", "and", "or", "on", "for"}
)

#: RRF smoothing constant (same semantics as the statute side, D-014).
_RRF_K = 60


def _lexical_tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _FLOOR_TOKENS}


class DocumentRetrievalService:
    """Hybrid (dense + lexical) retrieval over session-owned chunks."""

    def __init__(
        self,
        index: DocumentIndex,
        embedder: EmbeddingProvider,
        *,
        store: DocumentStore | None = None,
        top_k: int = 8,
        lexical_top_k: int = 10,
    ) -> None:
        self._index = index
        self._embedder = embedder
        self._store = store
        self._top_k = top_k
        self._lexical_top_k = lexical_top_k

    def retrieve(
        self,
        session_id: str,
        query: str,
        *,
        context_document_ids: list[str] | None = None,
    ) -> DocumentEvidence:
        """Search the session's documents only (isolation boundary, §21).

        Document-reference resolution (2026-09 task): "the first document",
        "the latest PDF", a filename mention, "the other document" (via
        conversation context) scope the search to the referenced documents
        deterministically; a genuinely ambiguous reference surfaces as a
        reason instead of a guessed hit. A query referencing several
        documents retrieves a balanced budget from EACH so comparisons are
        grounded in all sides.
        """
        documents = self._session_documents(session_id)
        if self._store is None:
            # No store wired (tests, minimal deployments): reference
            # resolution is unavailable, not "no documents" — search all.
            resolution = DocumentReferenceResolution(document_ids=None)
        else:
            resolution = resolve_document_references(
                query, documents, context_document_ids=context_document_ids
            )
        reasons: list[str] = list(resolution.notes)
        if resolution.ambiguous:
            from app.documents.references import AMBIGUOUS_REASON

            reasons.append(AMBIGUOUS_REASON)
            return DocumentEvidence(hits=[], reasons=reasons)
        if resolution.unresolved_reason:
            reasons.append(resolution.unresolved_reason)
            return DocumentEvidence(hits=[], reasons=reasons)
        if documents and resolution.document_ids is not None:
            missing = [d for d in resolution.document_ids if d not in {doc for doc, _ in documents}]
            if missing:
                reasons.append(resolution.unresolved_reason or "referenced document missing")
                return DocumentEvidence(hits=[], reasons=reasons)

        filter_ids = resolution.document_ids
        # Reference words carry no content signal: match on what remains.
        match_query = reference_free_query(query, documents) if filter_ids is not None else query
        groups = [filter_ids] if filter_ids else [None]
        # Each referenced document gets the FULL budget: a comparison of two
        # documents must see both sides' key clauses, not one crowded out.
        per_doc = self._top_k

        all_texts = self._index.texts(session_id)
        by_id = dict(documents)
        hits: list[DocumentHit] = []
        for group in groups:
            texts = (
                {cid: t for cid, t in all_texts.items() if cid.split("-p")[0] in set(group)}
                if group
                else all_texts
            )
            if not texts:
                continue
            query_vector = embed_query(self._embedder, match_query)
            dense_scored = self._index.search(
                session_id, query_vector, top_k=per_doc, document_ids=group
            )
            lexical_ranked = self._lexical_rank(match_query, texts)[: self._lexical_top_k]
            fused = self._rrf_fuse(dense_scored, lexical_ranked)
            dense_scores = dict(dense_scored)
            lexical_scores = dict(lexical_ranked)
            group_chunk_ids: list[str] = []
            for chunk_id, _fused_score in fused:
                text = texts.get(chunk_id)
                if text is None:
                    continue  # foreign or purged chunk: isolation holds
                group_chunk_ids.append(chunk_id)
            if not group_chunk_ids and group is not None:
                # Identity fallback: the user explicitly named these
                # documents; with no content match at all ("summarize the
                # latest document"), the document's own pages in order ARE
                # the evidence — retrieval must not refuse.
                group_chunk_ids = sorted(texts)[:per_doc]
            for chunk_id in group_chunk_ids:
                text = texts[chunk_id]
                document_id, page_start, page_end = _parse_chunk_id(chunk_id)
                # Confidence stays on the DENSE cosine scale (see class
                # docstring); a lexical-only or identity-fallback hit
                # reports its lexical score (or 0.0).
                score = dense_scores.get(chunk_id)
                if score is None:
                    score = lexical_scores.get(chunk_id, 0.0)
                hits.append(
                    DocumentHit(
                        chunk_id=chunk_id,
                        document_id=document_id,
                        text=text,
                        page_start=page_start,
                        page_end=page_end,
                        source_uri=(
                            f"document:{document_id}#page={page_start}" if page_start else None
                        ),
                        score=score,
                        filename=by_id.get(document_id),
                        position=(
                            by_id.get(document_id)
                            and [d for d, _ in documents].index(document_id) + 1
                        ),
                    )
                )
        hits.sort(key=lambda h: -h.score)
        return DocumentEvidence(
            hits=hits,
            reasons=reasons,
            reference_anchored=filter_ids is not None,
        )

    def _session_documents(self, session_id: str) -> list[tuple[str, str]]:
        """Upload-ordered (document_id, filename) of the session's READY
        documents; empty when no store is wired (resolution disabled)."""
        if self._store is None:
            return []
        return [
            (d.document_id, d.filename)
            for d in self._store.list_for_session(session_id)
            if d.status == JobStatus.READY
        ]

    def _lexical_rank(self, query: str, texts: dict[str, str]) -> list[tuple[str, float]]:
        """BM25-style token-overlap ranking over the session's chunk texts."""
        query_tokens = _lexical_tokens(query)
        if not query_tokens:
            return []
        scored: list[tuple[str, float]] = []
        for chunk_id, text in texts.items():
            tokens = _lexical_tokens(text)
            if not tokens:
                continue
            overlap = len(query_tokens & tokens)
            if overlap == 0:
                continue
            # Normalized overlap: fraction of query tokens present, with a
            # mild length penalty so a whole page is not guaranteed to beat
            # a focused paragraph that matches every query token.
            coverage = overlap / len(query_tokens)
            length_penalty = 1.0 / (1.0 + 0.01 * math.sqrt(len(tokens)))
            scored.append((chunk_id, coverage * length_penalty))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[: self._lexical_top_k]

    @staticmethod
    def _rrf_fuse(
        dense_scored: list[tuple[str, float]],
        lexical_ranked: list[tuple[str, float]],
    ) -> list[tuple[str, float]]:
        """Reciprocal-rank fusion of the dense and lexical candidate lists.

        Returns (chunk_id, fused_score) sorted descending, at most as many
        entries as the dense top-k (the retrieval budget).
        """
        if not dense_scored and not lexical_ranked:
            return []
        budget = len(dense_scored) if dense_scored else 0
        scores: dict[str, float] = {}
        for rank, (chunk_id, _score) in enumerate(dense_scored, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (_RRF_K + rank)
        for rank, (chunk_id, _score) in enumerate(lexical_ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (_RRF_K + rank)
        fused = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
        if budget:
            fused = fused[: max(budget, 0)]
        return fused


def _parse_chunk_id(chunk_id: str) -> tuple[str, int | None, int | None]:
    """``<document_id>-p0042-000`` → (document_id, 43, 43)."""
    marker = "-p"
    position = chunk_id.rfind(marker)
    if position == -1:
        return chunk_id, None, None
    document_id = chunk_id[:position]
    try:
        page = int(chunk_id[position + 2 : position + 6])
    except ValueError:
        return document_id, None, None
    return document_id, page, page
