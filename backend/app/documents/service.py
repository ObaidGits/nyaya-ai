"""Document service (plan 5.1/5.2/5.8/5.9): upload, status, list, delete.

Ownership is enforced on every operation (D-042): a foreign document is
indistinguishable from a missing one and surfaces as ``404`` (D-043,
ARCHITECTURE §21). Deletion purges metadata, the stored file and the
vectors (plan 5.9, D-018).

Jobs run asynchronously: the default runner schedules the ingestion
coroutine as a background task; a production arq worker (D-030) can call
``run_document_ingestion`` through the same seam.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Protocol

from app.core.errors import AppError
from app.documents.ingestion import DocumentWorkspace, run_document_ingestion
from app.documents.models import (
    DocumentJobStatus,
    DocumentListItem,
    UserDocument,
)
from app.documents.storage import DocumentFileStorage
from app.documents.store import DocumentStore
from app.documents.validation import validate_upload
from app.domain.models import JobStatus

logger = logging.getLogger(__name__)


class DocumentNotFoundError(AppError):
    """The document does not exist for this session (404, not 403)."""

    status_code = 404
    code = "DOCUMENT_NOT_FOUND"


class JobRunner(Protocol):
    """Async execution seam (arq/Redis in production, D-030)."""

    async def submit(
        self, workspace: DocumentWorkspace, document: UserDocument, pdf_bytes: bytes
    ) -> str: ...


class BackgroundJobRunner:
    """Run ingestion as an asyncio background task (local/dev/tests)."""

    async def submit(
        self, workspace: DocumentWorkspace, document: UserDocument, pdf_bytes: bytes
    ) -> str:
        task = asyncio.create_task(run_document_ingestion(workspace, document, pdf_bytes))
        # Let the caller observe the QUEUED state before the first stage.
        await asyncio.sleep(0)
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return document.job_id


_BACKGROUND_TASKS: set[asyncio.Task[UserDocument]] = set()


class EagerJobRunner:
    """Run ingestion to completion inline (deterministic tests)."""

    async def submit(
        self, workspace: DocumentWorkspace, document: UserDocument, pdf_bytes: bytes
    ) -> str:
        await run_document_ingestion(workspace, document, pdf_bytes)
        return document.job_id


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DocumentService:
    """Upload, observe and delete session-owned documents."""

    def __init__(
        self,
        store: DocumentStore,
        storage: DocumentFileStorage,
        workspace: DocumentWorkspace,
        *,
        runner: JobRunner,
        allowed_types: set[str],
        max_size_bytes: int,
    ) -> None:
        self._store = store
        self._storage = storage
        self._workspace = workspace
        self._runner = runner
        self._allowed_types = allowed_types
        self._max_size_bytes = max_size_bytes

    async def upload(
        self,
        *,
        session_id: str,
        filename: str,
        content_type: str | None,
        data: bytes,
    ) -> UserDocument:
        """Validate, store and enqueue one uploaded document (plan 5.1)."""
        validate_upload(
            filename=filename,
            content_type=content_type,
            data=data,
            allowed_types=self._allowed_types,
            max_size_bytes=self._max_size_bytes,
        )
        document_id = uuid.uuid4().hex
        job_id = uuid.uuid4().hex
        document = UserDocument(
            document_id=document_id,
            session_id=session_id,
            filename=filename,
            status=JobStatus.QUEUED,
            job_id=job_id,
            created_at=_now(),
            updated_at=_now(),
        )
        self._storage.save(session_id, document_id, data)
        self._store.put(document)
        await self._runner.submit(self._workspace, document, data)
        logger.info(
            "document uploaded",
            extra={
                "document_id": document_id,
                "session_id": session_id,
                "size_bytes": len(data),
            },
        )
        return document

    def status(self, *, session_id: str, document_id: str) -> DocumentJobStatus:
        document = self._require(session_id, document_id)
        return DocumentJobStatus(
            document_id=document.document_id,
            job_id=document.job_id,
            filename=document.filename,
            status=document.status,
            stages=[s.value for s in _remaining_stages(document.status)],
            error_code=document.error_code,
            error_message=document.error_message,
            page_count=document.page_count,
            chunk_count=document.chunk_count,
        )

    def list(self, *, session_id: str) -> list[DocumentListItem]:
        return [
            DocumentListItem(
                document_id=d.document_id,
                filename=d.filename,
                status=d.status,
                created_at=d.created_at,
                updated_at=d.updated_at,
                page_count=d.page_count,
                chunk_count=d.chunk_count,
                error_code=d.error_code,
            )
            for d in self._store.list_for_session(session_id)
        ]

    async def delete(self, *, session_id: str, document_id: str) -> None:
        """Purge metadata, file and vectors (plan 5.9; D-018)."""
        self._require(session_id, document_id)
        self._store.delete(document_id, session_id=session_id)
        self._storage.delete(session_id, document_id)
        removed = self._workspace.index.delete(session_id, document_id)
        logger.info(
            "document deleted",
            extra={
                "document_id": document_id,
                "session_id": session_id,
                "vectors_removed": removed,
            },
        )

    def _require(self, session_id: str, document_id: str) -> UserDocument:
        document = self._store.get(document_id, session_id=session_id)
        if document is None:
            raise DocumentNotFoundError("Document not found.")
        return document


def _remaining_stages(status: JobStatus) -> list[JobStatus]:
    from app.documents.ingestion import STAGES

    if status == JobStatus.FAILED:
        return []
    try:
        position = list(STAGES).index(status)
    except ValueError:
        return list(STAGES)
    return list(STAGES)[position:]
