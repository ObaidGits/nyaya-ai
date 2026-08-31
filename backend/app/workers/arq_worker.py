"""Production document-ingestion path: arq + Redis (DECISIONS D-030).

The API enqueues one arq job per upload (``ArqJobRunner``); the arq worker
container executes ``ingest_document`` against the same Redis-backed store
and index the API reads, so job status and vectors are shared across
processes (ARCHITECTURE §4.3). The PDF is re-read from the shared storage
volume, so the queue payload stays tiny.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import ClassVar, cast

from arq import create_pool
from arq.connections import RedisSettings
from redis import Redis

from app.core.config import Settings
from app.documents.ingestion import DocumentWorkspace
from app.documents.models import UserDocument

logger = logging.getLogger(__name__)


class ArqJobRunner:
    """Enqueue ingestion on the arq/Redis queue (production runner)."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    async def submit(
        self,
        workspace: DocumentWorkspace,
        document: UserDocument,
        pdf_bytes: bytes,  # unused: the worker re-reads from shared storage
    ) -> str:
        settings = RedisSettings.from_dsn(self._redis_url)
        pool = await create_pool(settings)
        try:
            await pool.enqueue_job(
                "ingest_document",
                document.session_id,
                document.document_id,
            )
        finally:
            await pool.aclose()
        return document.job_id


def build_production_workspace(settings: Settings) -> tuple[DocumentWorkspace, Redis]:
    """Assemble the Redis-backed workspace shared by API and worker.

    Returns the workspace and the raw Redis client (for health checks).
    """
    from app.documents.ingestion import DocumentWorkspace
    from app.documents.redis_index import RedisDocumentIndex
    from app.documents.redis_store import RedisDocumentStore
    from app.ingestion.embeddings import build_embedder

    client = Redis.from_url(settings.redis_url, decode_responses=True)
    store = RedisDocumentStore(client)
    index = RedisDocumentIndex(client)
    # Same embedder construction as the API (D-011/D-012): document vectors
    # and API query vectors must share one embedding space, or document
    # retrieval silently returns zero hits.
    workspace = DocumentWorkspace(store, index, build_embedder(settings.embedding_backend))
    return workspace, client


async def ingest_document(ctx: dict[str, object], session_id: str, document_id: str) -> str:
    """arq job: run the full ingestion lifecycle for one document."""
    from app.documents.ingestion import run_document_ingestion
    from app.documents.storage import DocumentFileStorage

    settings = cast(Settings, ctx["settings"])  # attached at worker startup
    workspace, _client = build_production_workspace(settings)
    store = workspace.store
    document = store.get(document_id, session_id=session_id)
    if document is None:
        logger.error("ingest job for unknown document", extra={"document_id": document_id})
        return document_id
    pdf_bytes = DocumentFileStorage(Path(settings.storage_dir)).load(session_id, document_id)
    await run_document_ingestion(workspace, document, pdf_bytes)
    return document_id


async def startup(ctx: dict[str, object]) -> None:
    from app.core.config import get_settings
    from app.core.logging import setup_logging

    settings = get_settings()
    setup_logging(settings)
    ctx["settings"] = settings
    logger.info("arq worker ready")


class WorkerSettings:
    """arq worker entrypoint (``arq app.workers.arq_worker.WorkerSettings``)."""

    functions: ClassVar[list[object]] = [ingest_document]
    on_startup = startup
    # REDIS_URL is exported by docker-compose; falls back to the dev default.
    redis_settings = RedisSettings.from_dsn(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    # Document ingestion embeds with BGE (~0.5 GB resident once warm); more
    # concurrent jobs multiply that peak. 2 keeps a small server safe while
    # uploads still process concurrently (configurable via WORKER_MAX_JOBS).
    max_jobs: ClassVar[int] = int(os.environ.get("WORKER_MAX_JOBS", "2"))
