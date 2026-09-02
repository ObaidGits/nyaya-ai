"""Raw retrieval endpoint (REQUIREMENTS D-019/D-020; ARCHITECTURE §38).

Useful for debugging and evaluation: exposes what retrieval returns for a
query — statute evidence, session document evidence, or both — with source
types kept distinguishable (A5-008). Sessions may only search their own
documents (A5-006/A5-007).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.api.v1.documents import get_session_id
from app.core.errors import AppError
from app.documents.retrieval import DocumentRetrievalService
from app.retrieval.models import MetadataFilter, RetrievalRoute, RetrievedEvidence
from app.retrieval.service import RetrievalService

router = APIRouter(prefix="/search", tags=["search"])


class SearchRequest(BaseModel):
    """Raw search request."""

    query: str = Field(min_length=1, max_length=4000)
    route: str | None = Field(
        default=None,
        description="Optional route override: statute | document | combined.",
    )
    chapter: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Restrict statute retrieval to this chapter (A3-008).",
    )
    section_number: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Restrict statute retrieval to this section (A3-010).",
    )
    act: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Restrict statute retrieval to this act's full name (A3-009).",
    )
    act_short: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description="Restrict statute retrieval to this act's short label (A3-009).",
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


def _build_filter(
    chapter: str | None, section_number: str | None, act: str | None, act_short: str | None
) -> MetadataFilter | None:
    """Assemble the server-side retrieval filter (A3-008/A3-009/A3-010).

    Returns None when no filter field was supplied (unchanged behavior).
    Unknown values are NOT an error here: the retrieval layer enforces the
    filter server-side and fails closed — an unknown chapter/section
    yields empty results, never an unfiltered fallback.
    """
    values: dict[str, str | None] = {
        "chapter": chapter,
        "section_number": section_number,
        "act": act,
        "act_short": act_short,
    }
    if all(v is None for v in values.values()):
        return None
    return MetadataFilter(**values)


def _resolve_route(query: str, route_override: str | None) -> RetrievalRoute:
    """Classify the route, honoring an explicit override and filter scope."""
    from app.retrieval.intent import classify_route

    route = classify_route(query)
    if route_override is None:
        return route
    try:
        return RetrievalRoute(route_override)
    except ValueError:
        raise AppError(
            "Invalid route. Use statute, document or combined.",
            status_code=422,
            code="INVALID_ROUTE",
        ) from None


def _execute_search(
    *,
    query: str,
    route_override: str | None,
    flt: MetadataFilter | None,
    session_id: str,
    statute: RetrievalService | None,
    documents: DocumentRetrievalService | None,
) -> SearchResponse:
    """Shared GET/POST logic: route, filter, retrieve, shape the response."""
    route = _resolve_route(query, route_override)
    if flt is not None and route == RetrievalRoute.DOCUMENT:
        if route_override is not None:
            # Explicitly asking for documents while filtering statute
            # metadata is a client bug, not an implicit scope hint.
            raise AppError(
                "Metadata filters apply to statute retrieval only.",
                status_code=422,
                code="INVALID_FILTER",
            )
        # A filter is an explicit statute-scope hint: do not let intent
        # classification silently drop it on the document route.
        route = RetrievalRoute.STATUTE

    response = SearchResponse(query=query)
    if route in (RetrievalRoute.STATUTE, RetrievalRoute.COMBINED):
        if statute is None:
            raise AppError(
                "Statute retrieval is not configured on this instance.",
                status_code=503,
                code="RETRIEVAL_NOT_CONFIGURED",
            )
        statute_evidence = statute.retrieve(query, flt)
        response.statute = _statute_view(statute_evidence)
    if route in (RetrievalRoute.DOCUMENT, RetrievalRoute.COMBINED):
        if documents is None:
            raise AppError(
                "Document retrieval is not configured on this instance.",
                status_code=503,
                code="DOCUMENTS_NOT_CONFIGURED",
            )
        document_evidence = documents.retrieve(session_id, query)
        response.documents = DocumentResults(hits=[h.model_dump() for h in document_evidence.hits])
    return response


@router.post("")
async def search(
    request: SearchRequest,
    session_id: Annotated[str, Depends(get_session_id)],
    statute: Annotated[RetrievalService | None, Depends(get_statute_retrieval)],
    documents: Annotated[DocumentRetrievalService | None, Depends(get_document_retrieval)],
) -> SearchResponse:
    """Raw retrieval for debugging/evaluation (D-019/D-020).

    Optional metadata filters (chapter, section_number, act, act_short)
    restrict statute retrieval (A3-008/A3-009/A3-010); they never widen
    it — unknown values return empty results.
    """
    flt = _build_filter(
        chapter=request.chapter,
        section_number=request.section_number,
        act=request.act,
        act_short=request.act_short,
    )
    return _execute_search(
        query=request.query,
        route_override=request.route,
        flt=flt,
        session_id=session_id,
        statute=statute,
        documents=documents,
    )


@router.get("")
async def search_get(
    session_id: Annotated[str, Depends(get_session_id)],
    statute: Annotated[RetrievalService | None, Depends(get_statute_retrieval)],
    documents: Annotated[DocumentRetrievalService | None, Depends(get_document_retrieval)],
    q: Annotated[str, Query(min_length=1, max_length=4000)],
    route: Annotated[str | None, Query(max_length=32)] = None,
    chapter: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    section_number: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    act: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    act_short: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
) -> SearchResponse:
    """GET variant of POST /api/v1/search: raw retrieval with optional
    metadata filters as query parameters (D-019/D-020, A3-008..A3-010).
    """
    flt = _build_filter(chapter, section_number, act, act_short)
    return _execute_search(
        query=q,
        route_override=route,
        flt=flt,
        session_id=session_id,
        statute=statute,
        documents=documents,
    )
