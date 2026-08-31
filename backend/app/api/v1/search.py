"""Raw retrieval endpoint (REQUIREMENTS D-019/D-020; ARCHITECTURE §38).

Useful for debugging and evaluation: exposes what retrieval returns for a
query — statute evidence, session document evidence, or both — with source
types kept distinguishable (A5-008). Sessions may only search their own
documents (A5-006/A5-007).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.v1.documents import get_session_id
from app.core.errors import AppError
from app.documents.retrieval import DocumentRetrievalService
from app.retrieval.models import RetrievedEvidence
from app.retrieval.service import RetrievalService

router = APIRouter(prefix="/search", tags=["search"])


class SearchRequest(BaseModel):
    """Raw search request."""

    query: str = Field(min_length=1, max_length=4000)
    route: str | None = Field(
        default=None,
        description="Optional route override: statute | document | combined.",
    )


class StatuteResults(BaseModel):
    source_type: str = "statute"
    route: str
    sufficient: bool
    confidence: float
    results: list[dict[str, object]]


class DocumentResults(BaseModel):
    source_type: str = "user_document"
    hits: list[dict[str, object]]


class SearchResponse(BaseModel):
    query: str
    statute: StatuteResults | None = None
    documents: DocumentResults | None = None


def get_statute_retrieval(request: Request) -> RetrievalService | None:
    return getattr(request.app.state, "retrieval_service", None)


def get_document_retrieval(request: Request) -> DocumentRetrievalService | None:
    return getattr(request.app.state, "document_retrieval_service", None)


def _statute_view(evidence: RetrievedEvidence) -> StatuteResults:
    return StatuteResults(
        route=evidence.route.value,
        sufficient=evidence.sufficient,
        confidence=evidence.confidence,
        results=[
            {
                "chunk_id": s.chunk.chunk_id,
                "act_short": s.chunk.act_short,
                "section_number": s.chunk.section_number,
                "section_title": s.chunk.section_title,
                "text": s.chunk.text,
                "page_start": s.chunk.page_start,
                "page_end": s.chunk.page_end,
                "source_uri": s.chunk.source_uri,
                "score": s.rrf_score,
                "retrieved_from": s.source,
            }
            for s in evidence.results
        ],
    )


@router.post("")
async def search(
    request: SearchRequest,
    session_id: Annotated[str, Depends(get_session_id)],
    statute: Annotated[RetrievalService | None, Depends(get_statute_retrieval)],
    documents: Annotated[DocumentRetrievalService | None, Depends(get_document_retrieval)],
) -> SearchResponse:
    """Raw retrieval for debugging/evaluation (D-019/D-020)."""
    from app.retrieval.intent import classify_route
    from app.retrieval.models import RetrievalRoute

    route = classify_route(request.query)
    if request.route is not None:
        try:
            route = RetrievalRoute(request.route)
        except ValueError:
            raise AppError(
                "Invalid route. Use statute, document or combined.",
                status_code=422,
                code="INVALID_ROUTE",
            ) from None

    response = SearchResponse(query=request.query)
    if route in (RetrievalRoute.STATUTE, RetrievalRoute.COMBINED):
        if statute is None:
            raise AppError(
                "Statute retrieval is not configured on this instance.",
                status_code=503,
                code="RETRIEVAL_NOT_CONFIGURED",
            )
        statute_evidence = statute.retrieve(request.query)
        response.statute = _statute_view(statute_evidence)
    if route in (RetrievalRoute.DOCUMENT, RetrievalRoute.COMBINED):
        if documents is None:
            raise AppError(
                "Document retrieval is not configured on this instance.",
                status_code=503,
                code="DOCUMENTS_NOT_CONFIGURED",
            )
        document_evidence = documents.retrieve(session_id, request.query)
        response.documents = DocumentResults(hits=[h.model_dump() for h in document_evidence.hits])
    return response
