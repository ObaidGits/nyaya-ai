"""In-memory document metadata + chunk store with ownership enforcement.

The store is the ownership boundary (ARCHITECTURE §21): every read takes
the session id and foreign documents are indistinguishable from missing
ones (404 semantics, §21 "Unauthorized access").

Production may swap this for PostgreSQL persistence (D-028); the service
layer depends only on this class's contract.
"""

from __future__ import annotations

from app.documents.models import DocumentChunk, UserDocument


class DocumentStore:
    """Session-scoped document metadata and chunk records."""

    def __init__(self) -> None:
        self._documents: dict[str, UserDocument] = {}
        self._chunks: dict[str, list[DocumentChunk]] = {}

    def put(self, document: UserDocument) -> None:
        self._documents[document.document_id] = document

    def update(self, document: UserDocument) -> None:
        self._documents[document.document_id] = document

    def get(self, document_id: str, *, session_id: str) -> UserDocument | None:
        """Owner-scoped read: foreign or unknown ids both return None."""
        document = self._documents.get(document_id)
        if document is None or document.session_id != session_id:
            return None
        return document

    def list_for_session(self, session_id: str) -> list[UserDocument]:
        documents = [d for d in self._documents.values() if d.session_id == session_id]
        return sorted(documents, key=lambda d: (d.created_at, d.document_id))

    def delete(self, document_id: str, *, session_id: str) -> UserDocument | None:
        """Owner-scoped delete; returns the removed record or None."""
        document = self.get(document_id, session_id=session_id)
        if document is None:
            return None
        del self._documents[document_id]
        self._chunks.pop(document_id, None)
        return document

    def put_chunks(self, document_id: str, chunks: list[DocumentChunk]) -> None:
        self._chunks[document_id] = chunks

    def get_chunks(self, document_id: str, *, session_id: str) -> list[DocumentChunk]:
        """Owner-scoped chunk read (defense in depth alongside the index)."""
        document = self.get(document_id, session_id=session_id)
        if document is None:
            return []
        return self._chunks.get(document_id, [])

    def chunk_count(self, document_id: str) -> int:
        return len(self._chunks.get(document_id, []))
