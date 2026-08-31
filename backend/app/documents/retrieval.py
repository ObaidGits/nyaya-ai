"""Session-scoped user-document retrieval (REQUIREMENTS A5-005..A5-012; §34/§35).

Searches only the requesting session's document index (§21: the session
filter is applied inside the index, never as a post-filter over global
results). Results carry document identity so statutory authority and
user-document evidence stay distinguishable (A5-008/A5-012).
"""

from __future__ import annotations

from app.documents.ingestion import DocumentIndex
from app.documents.models import DocumentEvidence, DocumentHit
from app.ingestion.embeddings import EmbeddingProvider
from app.retrieval.dense import embed_query


class DocumentRetrievalService:
    """Dense retrieval over session-owned document chunks."""

    def __init__(self, index: DocumentIndex, embedder: EmbeddingProvider, *, top_k: int = 5):
        self._index = index
        self._embedder = embedder
        self._top_k = top_k

    def retrieve(self, session_id: str, query: str) -> DocumentEvidence:
        """Search the session's documents only (isolation boundary, §21)."""
        query_vector = embed_query(self._embedder, query)
        scored = self._index.search(session_id, query_vector, top_k=self._top_k)
        hits: list[DocumentHit] = []
        for chunk_id, score in scored:
            text = self._index.get_text(session_id, chunk_id)
            if text is None:
                continue  # foreign or purged chunk: isolation holds
            document_id, page_start, page_end = _parse_chunk_id(chunk_id)
            hits.append(
                DocumentHit(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    text=text,
                    page_start=page_start,
                    page_end=page_end,
                    source_uri=f"document:{document_id}#page={page_start}" if page_start else None,
                    score=score,
                )
            )
        return DocumentEvidence(hits=hits)


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
