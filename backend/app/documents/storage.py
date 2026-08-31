"""Safe local file storage for uploaded documents (DECISIONS D-029).

Files live under ``<storage_dir>/documents/<session_id>/<document_id>.pdf``.
Document ids are generated server-side; session ids are validated against a
safe-path pattern so a hostile session header cannot traverse outside the
storage root (path traversal defense).
"""

from __future__ import annotations

import re
from pathlib import Path

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class DocumentStorageError(Exception):
    """Raised when storing or removing an uploaded file fails."""


def _safe_component(value: str) -> str:
    if not _SAFE_ID_RE.fullmatch(value):
        raise DocumentStorageError("Unsafe identifier in storage path.")
    return value


class DocumentFileStorage:
    """Session-scoped local storage for uploaded PDFs."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, session_id: str, document_id: str) -> Path:
        directory = self._root / "documents" / _safe_component(session_id)
        return directory / f"{_safe_component(document_id)}.pdf"

    def save(self, session_id: str, document_id: str, data: bytes) -> Path:
        """Persist one uploaded file and return its path."""
        path = self._path(session_id, document_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def load(self, session_id: str, document_id: str) -> bytes:
        return self._path(session_id, document_id).read_bytes()

    def exists(self, session_id: str, document_id: str) -> bool:
        return self._path(session_id, document_id).is_file()

    def delete(self, session_id: str, document_id: str) -> bool:
        """Remove the stored file; returns False when absent (idempotent)."""
        path = self._path(session_id, document_id)
        if path.is_file():
            path.unlink()
            return True
        return False
