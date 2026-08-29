"""Retrieval domain models (REQUIREMENTS A2-*/A3-*, ARCHITECTURE §9-§16).

Structures shared by the dense, sparse, hybrid, lookup and service layers.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.ingestion.models import Chunk


class RetrievalRoute(StrEnum):
    """Which corpus a query is answered from (ARCHITECTURE §14)."""

    STATUTE = "statute"
    DOCUMENT = "document"
    COMBINED = "combined"


class MetadataFilter(BaseModel):
    """Server-side retrieval filter (ARCHITECTURE §12, D-018).

    Applied inside the retrieval layer — never post-filtered in Python
    over a broad result set.
    """

    act: str | None = None
    act_short: str | None = None
    chapter: str | None = None
    section_number: str | None = None


class SectionIntent(BaseModel):
    """A detected direct section lookup (ARCHITECTURE §13, D-017)."""

    act_short: str | None = None
    section_number: str
    subsection: str | None = None


class ScoredChunk(BaseModel):
    """A chunk with retrieval scores attached."""

    chunk: Chunk
    rrf_score: float = 0.0
    dense_rank: int | None = None
    sparse_rank: int | None = None
    source: str = "hybrid"  # hybrid | dense | sparse | lookup


class RetrievedEvidence(BaseModel):
    """Structured retrieval evidence returned by the retrieval service."""

    query: str
    route: RetrievalRoute
    intent: SectionIntent | None = None
    results: list[ScoredChunk] = Field(default_factory=list)
    sufficient: bool = True
    confidence: float = 1.0
    reasons: list[str] = Field(default_factory=list)


class RetrievalError(Exception):
    """Raised when the retrieval layer cannot answer a query."""
