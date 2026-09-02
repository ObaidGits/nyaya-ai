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
        top_k: int = 8,
        lexical_top_k: int = 10,
    ) -> None:
        self._index = index
        self._embedder = embedder
        self._top_k = top_k
        self._lexical_top_k = lexical_top_k

    def retrieve(self, session_id: str, query: str) -> DocumentEvidence:
        """Search the session's documents only (isolation boundary, §21)."""
        query_vector = embed_query(self._embedder, query)
        dense_scored = self._index.search(session_id, query_vector, top_k=self._top_k)
        # Lexical pass over the same session-scoped texts: the index owns the
        # isolation (§21), so candidates are fetched through it, not globally.
        dense_scores = dict(dense_scored)
        texts = {
            chunk_id: text
            for chunk_id, _score in dense_scored
            if (text := self._index.get_text(session_id, chunk_id)) is not None
        }
        # Dense candidates alone are not enough for the lexical pass: every
        # session chunk must be considered, or exact-match chunks outside the
        # dense top-k can never surface (observed live: the suit's
        # title-page chunk named both parties and was never retrieved).
        for chunk_id in self._index.chunk_ids(session_id):
            if chunk_id not in texts:
                text = self._index.get_text(session_id, chunk_id)
                if text is not None:
                    texts[chunk_id] = text
        lexical_ranked = self._lexical_rank(query, texts)
        fused = self._rrf_fuse(dense_scored, lexical_ranked)
        hits: list[DocumentHit] = []
        for chunk_id, _fused_score in fused:
            text = self._index.get_text(session_id, chunk_id)
            if text is None:
                continue  # foreign or purged chunk: isolation holds
            document_id, page_start, page_end = _parse_chunk_id(chunk_id)
            # Confidence stays on the DENSE cosine scale: the sufficiency
            # gate threshold (document_confidence_threshold) is calibrated
            # to cosine similarity, not RRF magnitude. RRF orders the
            # ranking; the top hit's cosine decides sufficiency. A
            # lexical-only hit (no dense score) reports its lexical score
            # — bounded [0,1] like cosine and typically below the gate,
            # which is honest: pure lexical overlap without semantic
            # similarity is weak evidence.
            score = dense_scores.get(chunk_id)
            if score is None:
                score = dict(lexical_ranked).get(chunk_id, 0.0)
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
                )
            )
        return DocumentEvidence(hits=hits)

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
