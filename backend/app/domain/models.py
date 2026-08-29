"""Foundational domain contracts.

Only enums and contracts whose shape is directly supported by the project
documents are defined here (ARCHITECTURE.md §4.3, §21, §36; PRD §7.2, §15.2).
Full persistence models for sessions, conversations, messages, documents, jobs
and feedback are deliberately NOT built in Phase 1 — they arrive with the
phases that implement those features, so no speculative fields are invented.
"""

from enum import StrEnum

from pydantic import BaseModel


class JobStatus(StrEnum):
    """Lifecycle of an asynchronous ingestion job (ARCHITECTURE.md §4.3)."""

    QUEUED = "queued"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


#: Canonical stage order of a successful ingestion job (ARCHITECTURE.md §4.3).
JOB_PIPELINE_ORDER: tuple[JobStatus, ...] = (
    JobStatus.QUEUED,
    JobStatus.PARSING,
    JobStatus.CHUNKING,
    JobStatus.EMBEDDING,
    JobStatus.INDEXING,
    JobStatus.READY,
)


class MessageRole(StrEnum):
    """Roles in a chat conversation (ARCHITECTURE.md §36)."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class FeedbackVote(StrEnum):
    """Feedback vote values (PRD §15.2 / REQUIREMENTS.md D-026)."""

    UP = "up"
    DOWN = "down"


class CorpusType(StrEnum):
    """The two logically distinct retrieval corpora (ARCHITECTURE.md §2.3, §21).

    BNS is authoritative statutory material; user documents are evidence
    supplied by the user. They must never be conflated.
    """

    BNS = "bns"
    USER_DOCUMENT = "user_document"


class JobFailure(BaseModel):
    """Failure information attached to a failed ingestion job (ARCHITECTURE.md §4.3)."""

    error_code: str
    error_message: str
