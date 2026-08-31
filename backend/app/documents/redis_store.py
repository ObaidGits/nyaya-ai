"""Redis-backed document store (production worker path, D-030).

Same ownership contract as the in-memory store: every read is scoped by
session id and foreign documents are indistinguishable from missing ones.
Persisted as Redis hashes so the API process and the arq worker process
observe the same documents (ARCHITECTURE §4.3). Uses the synchronous Redis
client because the store seam is called from sync service code.
"""

from __future__ import annotations

import json

import redis

from app.documents.models import DocumentChunk, UserDocument
from app.documents.store import DocumentStore

_DOCUMENTS_KEY = "nyaya:documents"
_CHUNKS_KEY_PREFIX = "nyaya:docchunks:"


class RedisDocumentStore(DocumentStore):
    """Session-scoped document metadata and chunk records in Redis."""

    def __init__(self, client: redis.Redis) -> None:
        self._redis = client

    def put(self, document: UserDocument) -> None:
        self._redis.hset(_DOCUMENTS_KEY, document.document_id, document.model_dump_json())

    def update(self, document: UserDocument) -> None:
        self._redis.hset(_DOCUMENTS_KEY, document.document_id, document.model_dump_json())

    def get(self, document_id: str, *, session_id: str) -> UserDocument | None:
        """Owner-scoped read: foreign or unknown ids both return None."""
        raw: str | None = self._redis.hget(_DOCUMENTS_KEY, document_id)  # type: ignore[assignment]
        if raw is None:
            return None
        document = UserDocument.model_validate_json(raw)
        if document.session_id != session_id:
            return None
        return document

    def list_for_session(self, session_id: str) -> list[UserDocument]:
        values: list[str] = self._redis.hvals(_DOCUMENTS_KEY)  # type: ignore[assignment]
        documents = [
            UserDocument.model_validate_json(value)
            for value in values
            if UserDocument.model_validate_json(value).session_id == session_id
        ]
        return sorted(documents, key=lambda d: (d.created_at, d.document_id))

    def delete(self, document_id: str, *, session_id: str) -> UserDocument | None:
        """Owner-scoped delete; returns the removed record or None."""
        document = self.get(document_id, session_id=session_id)
        if document is None:
            return None
        self._redis.hdel(_DOCUMENTS_KEY, document_id)
        self._redis.delete(f"{_CHUNKS_KEY_PREFIX}{document_id}")
        return document

    def put_chunks(self, document_id: str, chunks: list[DocumentChunk]) -> None:
        payload = json.dumps([chunk.model_dump(mode="json") for chunk in chunks])
        self._redis.set(f"{_CHUNKS_KEY_PREFIX}{document_id}", payload)

    def get_chunks(self, document_id: str, *, session_id: str) -> list[DocumentChunk]:
        raw: str | None = self._redis.get(f"{_CHUNKS_KEY_PREFIX}{document_id}")  # type: ignore[assignment]
        if raw is None:
            return []
        return [DocumentChunk.model_validate(item) for item in json.loads(raw)]
