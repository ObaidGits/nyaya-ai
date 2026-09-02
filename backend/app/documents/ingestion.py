"""User-document ingestion job (REQUIREMENTS A5-002..A5-004; ARCHITECTURE §20).

Lifecycle per plan 5.3::

    QUEUED ─▶ PARSING ─▶ CHUNKING ─▶ EMBEDDING ─▶ INDEXING ─▶ READY
                                     (any stage) ─▶ FAILED + error_code

D-030 mandates arq + Redis for production. The job here is a pure async
function over an injectable ``DocumentWorkspace`` (store + storage + index)
so:

* the production arq worker calls ``run_document_ingestion`` directly, and
* tests (and local runs without Redis) drive it without any queue.

PDF parsing reuses the Phase 2 extractor (plan 5.4); embedding reuses the
ingestion embedding seam; indexing goes to a session-scoped document index
that is logically (and by collection/namespace) separate from the
authoritative statute corpus (A5-008).
"""

from __future__ import annotations

import logging

from app.documents.chunker import chunk_document_pages
from app.documents.models import UserDocument
from app.documents.store import DocumentStore
from app.domain.models import JobStatus
from app.ingestion.embeddings import EmbeddingProvider
from app.ingestion.extract import PypdfPageExtractor

logger = logging.getLogger(__name__)

#: Stage order mirrored by the status API (D-012..D-014).
STAGES: tuple[JobStatus, ...] = (
    JobStatus.PARSING,
    JobStatus.CHUNKING,
    JobStatus.EMBEDDING,
    JobStatus.INDEXING,
    JobStatus.READY,
)

# Client-safe failure codes; parser internals never reach the API.
ERROR_PARSE_FAILED = "PARSE_FAILED"
ERROR_EMPTY_DOCUMENT = "EMPTY_DOCUMENT"
ERROR_EMBED_FAILED = "EMBED_FAILED"
ERROR_INDEX_FAILED = "INDEX_FAILED"


class DocumentIndex:
    """Session-scoped vector index seam for user-document chunks.

    Implementations must apply the session filter inside the search
    (ARCHITECTURE §21: never retrieve globally and filter afterward).
    """

    def upsert(
        self,
        session_id: str,
        document_id: str,
        chunks: list[tuple[str, str, list[float]]],
    ) -> int:
        """Store (chunk_id, text, vector) triples scoped to the document."""
        raise NotImplementedError

    def delete(self, session_id: str, document_id: str) -> int:
        """Purge all vectors of one document. Returns records removed."""
        raise NotImplementedError

    def search(
        self,
        session_id: str,
        query_vector: list[float],
        *,
        top_k: int,
        document_ids: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Return (chunk_id, score) pairs for the session's documents only."""
        raise NotImplementedError

    def get_text(self, session_id: str, chunk_id: str) -> str | None:
        """Owner-scoped chunk text lookup (source drawer payload)."""
        raise NotImplementedError

    def chunk_ids(self, session_id: str) -> list[str]:
        """Every chunk id stored for the session (lexical candidate pool)."""
        raise NotImplementedError


class DocumentWorkspace:
    """Everything an ingestion job needs, injectable for tests/arq."""

    def __init__(
        self,
        store: DocumentStore,
        index: DocumentIndex,
        embedder: EmbeddingProvider,
    ) -> None:
        self.store = store
        self.index = index
        self.embedder = embedder


class _InMemoryDocumentIndex(DocumentIndex):
    """In-process cosine index keyed by session (tests, local dev).

    Same isolation contract as the Qdrant implementation: searches only ever
    see the requesting session's chunks.
    """

    def __init__(self) -> None:
        self._vectors: dict[str, dict[str, list[float]]] = {}  # session -> chunk -> vec
        self._texts: dict[str, str] = {}  # chunk_id -> text (global ids are unique)

    def upsert(
        self,
        session_id: str,
        document_id: str,
        chunks: list[tuple[str, str, list[float]]],
    ) -> int:
        session_vectors = self._vectors.setdefault(session_id, {})
        for chunk_id, _text, vector in chunks:
            session_vectors[chunk_id] = vector
            self._texts[chunk_id] = _text
        return len(chunks)

    def delete(self, session_id: str, document_id: str) -> int:
        session_vectors = self._vectors.get(session_id, {})
        removed = [cid for cid in session_vectors if cid.startswith(f"{document_id}-")]
        for cid in removed:
            session_vectors.pop(cid, None)
            self._texts.pop(cid, None)
        return len(removed)

    def search(
        self,
        session_id: str,
        query_vector: list[float],
        *,
        top_k: int,
        document_ids: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        session_vectors = self._vectors.get(session_id, {})
        candidates: list[tuple[str, list[float]]] = list(session_vectors.items())
        if document_ids is not None:
            allowed = set(document_ids)
            candidates = [(cid, v) for cid, v in candidates if cid.split("-p")[0] in allowed]
        scored = sorted(
            ((cid, _cosine(query_vector, vec)) for cid, vec in candidates),
            key=lambda pair: (-pair[1], pair[0]),
        )
        return [(cid, score) for cid, score in scored[:top_k] if score > 0]

    def get_text(self, session_id: str, chunk_id: str) -> str | None:
        if chunk_id not in self._vectors.get(session_id, {}):
            return None
        return self._texts.get(chunk_id)

    def chunk_ids(self, session_id: str) -> list[str]:
        return list(self._vectors.get(session_id, {}))


def _cosine(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _fail(
    workspace: DocumentWorkspace,
    document: UserDocument,
    *,
    code: str,
    message: str,
) -> None:
    document.status = JobStatus.FAILED
    document.error_code = code
    document.error_message = message
    workspace.store.update(document)


async def run_document_ingestion(
    workspace: DocumentWorkspace,
    document: UserDocument,
    pdf_bytes: bytes,
) -> UserDocument:
    """Run the full async ingestion lifecycle for one document.

    Raises never: failures are recorded on the document record with
    client-safe codes (plan 5.3 failure path).
    """
    import tempfile

    from app.core.errors import AppError as _AppError

    try:
        # --- PARSING (reuses the Phase 2 extractor, plan 5.4) -------------
        document.status = JobStatus.PARSING
        workspace.store.update(document)
        extractor = PypdfPageExtractor()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
            tmp.write(pdf_bytes)
            tmp.flush()
            pages = extractor.extract(tmp.name)
        document.page_count = len(pages)
        workspace.store.update(document)

        # --- CHUNKING (document strategy, plan 5.5) ------------------------
        document.status = JobStatus.CHUNKING
        workspace.store.update(document)
        chunks = chunk_document_pages(
            pages,
            document_id=document.document_id,
            session_id=document.session_id,
            source_uri=f"document:{document.document_id}",
        )
        if not chunks:
            _fail(
                workspace,
                document,
                code=ERROR_EMPTY_DOCUMENT,
                message="The document contains no extractable text.",
            )
            return document

        # --- EMBEDDING (reuses the embedding seam, plan 5.6) ---------------
        document.status = JobStatus.EMBEDDING
        workspace.store.update(document)
        vectors = workspace.embedder.embed_texts([c.text for c in chunks])

        # --- INDEXING (session-scoped document index, A5-008) --------------
        document.status = JobStatus.INDEXING
        workspace.store.update(document)
        workspace.index.upsert(
            document.session_id,
            document.document_id,
            [(c.chunk_id, c.text, vec) for c, vec in zip(chunks, vectors, strict=True)],
        )
    except _AppError as exc:
        # ExtractionError ("no usable text layer") and sibling app errors:
        # client-safe message, no internals.
        logger.info(
            "document ingestion rejected content",
            extra={"document_id": document.document_id, "code": getattr(exc, "code", None)},
        )
        _fail(
            workspace,
            document,
            code=ERROR_EMPTY_DOCUMENT,
            message="The document contains no extractable text.",
        )
        return document
    except Exception:
        logger.exception(
            "document ingestion failed",
            extra={"document_id": document.document_id, "session_id": document.session_id},
        )
        _fail(
            workspace,
            document,
            code=ERROR_PARSE_FAILED,
            message="The document could not be processed.",
        )
        return document

    document.chunk_count = len(chunks)
    document.status = JobStatus.READY
    document.error_code = None
    document.error_message = None
    workspace.store.update(document)
    logger.info(
        "document ingestion complete",
        extra={
            "document_id": document.document_id,
            "session_id": document.session_id,
            "chunks": len(chunks),
            "pages": document.page_count,
        },
    )
    return document
