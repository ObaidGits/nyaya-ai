"""Production worker-path tests (arq + Redis, D-030).

Uses an in-process fake Redis to verify the store/index ownership contract,
the enqueue path, and the full ingest_document lifecycle deterministically.
Live Redis verification happens in the Phase 10 compose smoke test.
"""

from __future__ import annotations

from typing import Any

from app.documents.models import DocumentChunk, UserDocument
from app.documents.redis_index import RedisDocumentIndex
from app.documents.redis_store import RedisDocumentStore
from app.domain.models import JobStatus
from tests.documents.pdf_fixtures import make_pdf

SESSION = "worker-session-0001"
OTHER = "other-session-0002"

NOTICE = "The tenant must give 30 days written notice before vacating."


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, Any, Any]] = []

    def hset(self, key: str, field: str, value: str) -> None:
        self._ops.append(("hset", (key, field, value), None))

    def hdel(self, key: str, *fields: str) -> None:
        self._ops.append(("hdel", (key, fields), None))

    def set(self, key: str, value: str) -> None:
        self._ops.append(("set", (key, value), None))

    def delete(self, key: str) -> None:
        self._ops.append(("del", (key,), None))

    def execute(self) -> None:
        for op, args, _ in self._ops:
            if op == "hset":
                key, field, value = args
                self._redis.hset(key, field, value)
            elif op == "hdel":
                key, fields = args
                self._redis.hdel(key, *fields)
            elif op == "set":
                key, value = args
                self._redis.set(key, value)
            elif op == "del":
                (key,) = args
                self._redis.delete(key)
        self._ops = []


class FakeRedis:
    """Minimal sync Redis surface used by the store/index."""

    def __init__(self) -> None:
        self.hash_store: dict[str, dict[str, str]] = {}
        self.kv: dict[str, str] = {}

    def hset(self, key: str, field: str, value: str) -> None:
        self.hash_store.setdefault(key, {})[field] = value

    def hget(self, key: str, field: str) -> str | None:
        return self.hash_store.get(key, {}).get(field)

    def hvals(self, key: str) -> list[str]:
        return list(self.hash_store.get(key, {}).values())

    def hdel(self, key: str, *fields: str) -> None:
        for field in fields:
            self.hash_store.get(key, {}).pop(field, None)

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hash_store.get(key, {}))

    def set(self, key: str, value: str) -> None:
        self.kv[key] = value

    def get(self, key: str) -> str | None:
        return self.kv.get(key)

    def delete(self, key: str) -> None:
        self.kv.pop(key, None)

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)


def _document(document_id: str = "d1", session: str = SESSION) -> UserDocument:
    return UserDocument(
        document_id=document_id,
        session_id=session,
        filename="notice.pdf",
        status=JobStatus.QUEUED,
        job_id="j1",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


class TestRedisDocumentStore:
    def test_put_get_owner_scoped(self) -> None:
        store = RedisDocumentStore(FakeRedis())  # type: ignore[arg-type]
        document = _document()
        store.put(document)
        assert store.get("d1", session_id=SESSION) == document
        # Foreign session: indistinguishable from missing.
        assert store.get("d1", session_id=OTHER) is None
        assert store.get("missing", session_id=SESSION) is None

    def test_list_and_delete_are_owner_scoped(self) -> None:
        store = RedisDocumentStore(FakeRedis())  # type: ignore[arg-type]
        store.put(_document("d1"))
        store.put(_document("d2", session=OTHER))
        assert [d.document_id for d in store.list_for_session(SESSION)] == ["d1"]
        assert store.delete("d1", session_id=OTHER) is None  # no leak
        assert store.delete("d1", session_id=SESSION) is not None
        assert store.list_for_session(SESSION) == []

    def test_chunks_roundtrip(self) -> None:
        store = RedisDocumentStore(FakeRedis())  # type: ignore[arg-type]
        chunk = DocumentChunk(
            chunk_id="d1-p1-000",
            document_id="d1",
            session_id=SESSION,
            page_start=1,
            page_end=1,
            text=NOTICE,
            source_uri="document:d1#page=1",
        )
        store.put_chunks("d1", [chunk])
        assert store.get_chunks("d1", session_id=SESSION) == [chunk]


class TestRedisDocumentIndex:
    def test_search_is_session_isolated(self) -> None:
        index = RedisDocumentIndex(FakeRedis())  # type: ignore[arg-type]
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, [1.0, 0.0])])
        index.upsert(OTHER, "d2", [("d2-p1-000", "foreign", [1.0, 0.0])])
        hits = index.search(SESSION, [1.0, 0.0], top_k=5)
        assert [chunk_id for chunk_id, _score in hits] == ["d1-p1-000"]
        assert index.get_text(SESSION, "d1-p1-000") == NOTICE
        assert index.get_text(SESSION, "d2-p1-000") is None

    def test_delete_purges_document_vectors(self) -> None:
        index = RedisDocumentIndex(FakeRedis())  # type: ignore[arg-type]
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, [1.0, 0.0])])
        assert index.delete(SESSION, "d1") == 1
        assert index.search(SESSION, [1.0, 0.0], top_k=5) == []


class TestIngestDocumentJob:
    async def test_full_lifecycle_reaches_ready_and_indexes(self, tmp_path: object) -> None:
        import asyncio
        from pathlib import Path

        from app.core.config import Settings
        from app.documents.storage import DocumentFileStorage
        from app.workers.arq_worker import ingest_document

        settings = Settings(
            _env_file=None,
            storage_dir=str(tmp_path),
            redis_url="redis://unused:6379/0",
        )
        storage = DocumentFileStorage(Path(str(tmp_path)))
        pdf_bytes = make_pdf([NOTICE])
        storage.save(SESSION, "d1", pdf_bytes)

        # Build the workspace against the fake client, then run the job.
        fake = FakeRedis()
        from app.documents.ingestion import DocumentWorkspace
        from app.ingestion.embeddings import HashingEmbedder

        workspace = DocumentWorkspace(
            RedisDocumentStore(fake),  # type: ignore[arg-type]
            RedisDocumentIndex(fake),  # type: ignore[arg-type]
            HashingEmbedder(),
        )
        workspace.store.put(_document())

        ctx: dict[str, object] = {"settings": settings}
        # Patch the workspace factory to reuse the fake-backed one.
        import app.workers.arq_worker as worker_module

        original = worker_module.build_production_workspace

        def fake_build(_settings: Settings) -> object:
            return workspace, fake

        worker_module.build_production_workspace = fake_build  # type: ignore[assignment]
        try:
            await asyncio.to_thread(lambda: asyncio.run(ingest_document(ctx, SESSION, "d1")))
        finally:
            worker_module.build_production_workspace = original  # type: ignore[assignment]
        document = workspace.store.get("d1", session_id=SESSION)
        assert document is not None
        assert document.status == JobStatus.READY
        # Vectors are searchable through the shared index.
        from app.documents.retrieval import embed_query

        query_vector = embed_query(HashingEmbedder(), "What notice must the tenant give?")
        hits = workspace.index.search(SESSION, query_vector, top_k=5)
        assert hits, "indexed document chunks must be searchable"

    async def test_unknown_document_is_reported_not_crashed(self) -> None:
        import app.workers.arq_worker as worker_module
        from app.core.config import Settings

        settings = Settings(_env_file=None)
        fake = FakeRedis()
        from app.documents.ingestion import DocumentWorkspace
        from app.ingestion.embeddings import HashingEmbedder

        workspace = DocumentWorkspace(
            RedisDocumentStore(fake),  # type: ignore[arg-type]
            RedisDocumentIndex(fake),  # type: ignore[arg-type]
            HashingEmbedder(),
        )
        ctx: dict[str, object] = {"settings": settings}
        original = worker_module.build_production_workspace
        worker_module.build_production_workspace = lambda _s: (workspace, fake)  # type: ignore[assignment]
        try:
            result = await worker_module.ingest_document(ctx, SESSION, "no-such-doc")
        finally:
            worker_module.build_production_workspace = original  # type: ignore[assignment]
        assert result == "no-such-doc"


class TestArqJobRunner:
    async def test_submit_enqueues_ingest_document_job(self) -> None:
        from app.workers.arq_worker import ArqJobRunner

        enqueued: list[tuple[str, tuple[object, ...]]] = []

        class FakePool:
            async def enqueue_job(self, function: str, *args: object) -> None:
                enqueued.append((function, args))

            async def aclose(self) -> None:
                return None

        import app.workers.arq_worker as worker_module

        async def fake_create_pool(_settings: object) -> FakePool:
            return FakePool()

        original = worker_module.create_pool
        worker_module.create_pool = fake_create_pool  # type: ignore[assignment]
        try:
            runner = ArqJobRunner("redis://localhost:6379/0")
            job_id = await runner.submit(None, _document(), b"%PDF-irrelevant")  # type: ignore[arg-type]
        finally:
            worker_module.create_pool = original  # type: ignore[assignment]
        assert job_id == "j1"
        assert enqueued == [("ingest_document", (SESSION, "d1"))]


class TestEmbeddingParity:
    """Worker-embedded vectors must be retrievable by the API service (D-030).

    The worker and the API build their embedders from the SAME factory
    (``app.ingestion.embeddings.build_embedder``) reading the SAME
    ``EMBEDDING_BACKEND`` setting, so dimensions can only diverge when one
    process degraded to the hashing fallback. These tests pin both halves
    of that contract.
    """

    def test_same_embedder_roundtrip(self) -> None:
        """Doc embedded by the worker path is found by the API retrieval path."""
        from app.documents.retrieval import DocumentRetrievalService
        from app.ingestion.embeddings import HashingEmbedder

        embedder = HashingEmbedder()  # same instance contract as build_embedder
        fake = FakeRedis()
        index = RedisDocumentIndex(fake)  # type: ignore[arg-type]
        # Worker side: embed chunk text WITHOUT the query prefix (passages).
        vector = embedder.embed_texts([NOTICE])[0]
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, vector)])
        # API side: query WITH the prefix, via the real query path.
        service = DocumentRetrievalService(index, embedder)  # type: ignore[arg-type]
        evidence = service.retrieve(SESSION, "30 days written notice")
        assert [h.chunk_id for h in evidence.hits] == ["d1-p1-000"]
        assert evidence.hits[0].document_id == "d1"

    def test_dimension_mismatch_fails_loud_not_silent(self) -> None:
        """A fully mismatched index raises EMBEDDING_MISMATCH, not empty hits."""
        import pytest
        from app.ingestion.embeddings import EmbeddingError

        index = RedisDocumentIndex(FakeRedis())  # type: ignore[arg-type]
        # Worker embedded at 768 dims (BGE); API query is 256 (hashing).
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, [0.1] * 768)])
        with pytest.raises(EmbeddingError):
            index.search(SESSION, [0.1] * 256, top_k=5)

    def test_partial_mismatch_skips_bad_vectors(self) -> None:
        """One bad vector among good ones is skipped, good ones still found."""
        index = RedisDocumentIndex(FakeRedis())  # type: ignore[arg-type]
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, [1.0, 0.0])])
        index.upsert(SESSION, "d2", [("d2-p1-000", "stale", [0.5] * 768)])
        hits = index.search(SESSION, [1.0, 0.0], top_k=5)
        assert [chunk_id for chunk_id, _score in hits] == ["d1-p1-000"]
