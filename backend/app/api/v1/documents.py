"""Document API endpoints (REQUIREMENTS D-010..D-018, D-040..D-048; ARCHITECTURE §20-§22, §38).

Anonymous session-token identity (D-040/D-041): the client supplies
``X-Session-Id``; when absent the server issues one in the response so the
first request bootstraps a session. Ownership is enforced server-side on
every read/write (D-042) and foreign documents surface as 404 (D-043).
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, UploadFile
from fastapi.responses import JSONResponse

from app.core.errors import AppError
from app.core.rate_limit import UPLOAD_SCOPE, enforce_rate_limit
from app.documents.models import DocumentJobStatus, DocumentListItem
from app.documents.service import DocumentService
from app.documents.validation import UploadRejectedError
from app.observability.metrics import UPLOADS

router = APIRouter(prefix="/documents", tags=["documents"])

SESSION_HEADER = "X-Session-Id"
_SAFE_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class SessionMissingError(AppError):
    """The request carries no usable session identity."""

    status_code = 400
    code = "SESSION_REQUIRED"


def get_session_id(
    x_session_id: Annotated[str | None, Header(alias="X-Session-Id")] = None,
) -> str:
    """Resolve the ownership identity (D-040).

    A missing or malformed session id is rejected: ownership must be
    explicit, never defaulted to a shared identity.
    """
    if x_session_id is None or not _SAFE_SESSION_RE.fullmatch(x_session_id):
        raise SessionMissingError(
            "A valid X-Session-Id header is required.",
            code="SESSION_REQUIRED",
        )
    return x_session_id


def get_document_service(request: Request) -> DocumentService:
    from typing import cast

    service = cast(DocumentService | None, getattr(request.app.state, "document_service", None))
    if service is None:
        raise AppError(
            "Documents are not configured on this instance.",
            status_code=503,
            code="DOCUMENTS_NOT_CONFIGURED",
        )
    return service


class UploadResponse(JSONResponse):
    """Upload result carrying the session token when one was issued."""

    def __init__(self, payload: dict[str, object], session_id: str) -> None:
        super().__init__(status_code=201, content=payload)
        self.headers[SESSION_HEADER] = session_id


@router.post("/upload")
async def upload_document(
    request: Request,
    file: UploadFile,
    session_id: Annotated[str, Depends(get_session_id)],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> UploadResponse:
    """Validate and enqueue one uploaded PDF (D-010, D-050)."""
    _enforce_upload_limit(request, session_id)
    settings = getattr(request.app.state, "settings", None)
    max_bytes = (
        settings.max_upload_size_mb * 1024 * 1024 if settings is not None else 20 * 1024 * 1024
    )
    data = await _read_upload(file, max_bytes)
    UPLOADS.inc()
    document = await service.upload(
        session_id=session_id,
        filename=file.filename or "",
        content_type=file.content_type,
        data=data,
    )
    return UploadResponse(
        {
            "document_id": document.document_id,
            "job_id": document.job_id,
            "status": document.status.value,
        },
        session_id,
    )


@router.get("/{document_id}/status")
def document_status(
    document_id: str,
    session_id: Annotated[str, Depends(get_session_id)],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> DocumentJobStatus:
    """Job/state view with parse/chunk/embed progress (D-011..D-014)."""
    return service.status(session_id=session_id, document_id=document_id)


@router.get("")
def list_documents(
    session_id: Annotated[str, Depends(get_session_id)],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> list[DocumentListItem]:
    """Session-scoped document list (D-015/D-016)."""
    return service.list(session_id=session_id)


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: str,
    session_id: Annotated[str, Depends(get_session_id)],
    service: Annotated[DocumentService, Depends(get_document_service)],
) -> None:
    """Purge metadata, file and vectors (D-017/D-018)."""
    await service.delete(session_id=session_id, document_id=document_id)


def _enforce_upload_limit(request: Request, session_id: str) -> None:
    """Reject excessive uploads per session (D-050)."""
    limiter = getattr(request.app.state, "rate_limiter", None)
    settings = getattr(request.app.state, "settings", None)
    if limiter is None or settings is None:
        return
    enforce_rate_limit(
        limiter,
        scope=UPLOAD_SCOPE,
        key=session_id,
        limit=settings.rate_limit_upload_per_minute,
        window_seconds=60.0,
    )


async def _read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """Read the upload body in bounded chunks (oversized payloads are cut off).

    Reading the whole body with one ``read()`` would buffer an arbitrarily
    large request before the size check runs; reading in chunks caps memory
    at one chunk past the limit and rejects early.
    """
    buffer = bytearray()
    while True:
        chunk = await file.read(_CHUNK_BYTES)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise UploadRejectedError(
                f"The uploaded file exceeds the maximum size of {max_bytes // (1024 * 1024)} MB.",
                code="FILE_TOO_LARGE",
            )
    return bytes(buffer)


_CHUNK_BYTES = 1024 * 1024
