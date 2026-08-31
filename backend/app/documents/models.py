"""User-document domain models (REQUIREMENTS A5-*; ARCHITECTURE §20-§23).

Uploaded documents are untrusted user evidence (§23) — never authoritative
statutory material. Every record and chunk carries the owning session id so
isolation is enforced at the data-access boundary (§21), not by
post-filtering retrieval results.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.models import JobStatus


class UserDocument(BaseModel):
    """Metadata record for one uploaded document (plan 5.2)."""

    document_id: str
    session_id: str
    filename: str
    status: JobStatus = JobStatus.QUEUED
    job_id: str
    error_code: str | None = None
    error_message: str | None = None
    page_count: int | None = None
    chunk_count: int | None = None
    created_at: str = ""
    updated_at: str = ""


class DocumentChunk(BaseModel):
    """One retrievable chunk of a session-owned document (A5-003)."""

    chunk_id: str
    document_id: str
    session_id: str
    page_start: int
    page_end: int
    text: str
    source_uri: str


class DocumentJobStatus(BaseModel):
    """Status view for the status API (D-011..D-014, plan 5.12).

    Exposes the full stage lifecycle plus client-safe failure information —
    internal exception details never leave the server.
    """

    document_id: str
    job_id: str
    filename: str
    status: JobStatus
    stages: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    page_count: int | None = None
    chunk_count: int | None = None


class DocumentListItem(BaseModel):
    """One entry of the session-scoped document list (D-015/D-016)."""

    document_id: str
    filename: str
    status: JobStatus
    created_at: str = ""
    updated_at: str = ""
    page_count: int | None = None
    chunk_count: int | None = None
    error_code: str | None = None


class DocumentHit(BaseModel):
    """One retrieved user-document chunk with full traceability (A5-012)."""

    chunk_id: str
    document_id: str
    text: str
    page_start: int | None = None
    page_end: int | None = None
    source_uri: str | None = None
    score: float = 0.0


class DocumentEvidence(BaseModel):
    """Retrieved evidence from one session's documents (§34)."""

    hits: list[DocumentHit] = Field(default_factory=list)

    @property
    def sufficient(self) -> bool:
        return bool(self.hits)
