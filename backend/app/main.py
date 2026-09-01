"""Nyaya backend application entry point.

Creates and configures the FastAPI application: structured logging, request
ID middleware, consistent error handling, and the versioned API router.
Domain logic lives in the ``api``, ``core``, ``domain``, ``generation``,
``llm`` and ``retrieval`` packages; this module only assembles the
application.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.admin.store import AdminSettingsStore
from app.api.v1.router import api_router
from app.core.config import APP_VERSION, Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.health import (
    ActiveModelCheck,
    CheckRegistry,
    ConfigurationCheck,
    StorageCheck,
    VectorDBCheck,
)
from app.core.logging import setup_logging
from app.core.rate_limit import RateLimiter
from app.core.request_id import RequestIDMiddleware
from app.ingestion.embeddings import EmbeddingProvider
from app.language.detection import detector_for_backend
from app.language.service import LanguageService
from app.llm.registry import create_default_registry
from app.observability.metrics import (
    REQUEST_LATENCY,
    REQUESTS,
    VECTOR_DB_UP,
    seed_default_series,
)

if TYPE_CHECKING:
    from app.documents.retrieval import DocumentRetrievalService

API_V1_PREFIX = "/api/v1"

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "Backend API for Nyaya, a legal assistant over the Bharatiya Nyaya "
    "Sanhita (BNS). This is the application foundation; chat, documents, "
    "forms, search and feedback endpoints are added in later implementation "
    "phases."
)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Explicit settings (used by tests); defaults to the
            process-wide settings loaded from the environment.
    """
    if settings is None:
        settings = get_settings()
    setup_logging(settings)
    seed_default_series()

    app = FastAPI(
        title="Nyaya API",
        description=DESCRIPTION,
        version=APP_VERSION,
    )

    # Admin configuration store (D-080): persisted admin settings override
    # environment values. env_settings keeps the pre-override snapshot so the
    # console can tell "env" from "console" secret origins and fall back to
    # the environment value when a console secret is explicitly removed.
    admin_store = (
        AdminSettingsStore(settings.admin_settings_path)
        if settings.admin_settings_path
        else AdminSettingsStore("")  # no-op persistence (path unset)
    )
    env_settings = settings
    settings = admin_store.apply_overrides(settings)

    # Injectable application state (no global mutable singletons in handlers).
    app.state.settings = settings
    app.state.env_settings = env_settings
    app.state.admin_store = admin_store
    app.state.llm_registry = create_default_registry()
    app.state.rate_limiter = RateLimiter()

    # ActiveModelCheck resolves the provider from app.state AT CHECK TIME, so
    # the readiness "model" check always reflects the console's current
    # provider (a startup-time check would go stale the moment settings are
    # saved) and uses the provider's authenticated probe, never a bare ping.
    def _resolve_active_provider() -> "object":
        from app.core.errors import LLMProviderNotConfiguredError
        from app.llm.registry import UnknownProviderError

        current = app.state.settings
        try:
            return app.state.llm_registry.create(current.llm_provider, current)
        except UnknownProviderError as exc:
            raise LLMProviderNotConfiguredError(str(exc)) from exc

    # Redis is a real dependency only when the deployment uses it (D-030);
    # in-memory deployments must not report a phantom dependency.
    checks = [
        ConfigurationCheck(settings),
        VectorDBCheck(settings.qdrant_url),
        ActiveModelCheck(_resolve_active_provider),
        StorageCheck(settings),
    ]
    if settings.documents_backend == "redis":
        from app.core.health import RedisCheck

        checks.append(RedisCheck(settings.redis_url))
    app.state.check_registry = CheckRegistry(checks)
    app.state.document_service, app.state.document_retrieval_service = _build_documents(settings)
    app.state.retrieval_service = build_retrieval_service(
        settings, document_retrieval=app.state.document_retrieval_service
    )
    app.state.forms_library = _build_forms_library(settings)
    # Language handling seam (D-077): script detection by default; the
    # fastText backend is opt-in via settings and never required.
    detector = None
    if settings.language_detection_backend == "fasttext":
        if not settings.fasttext_model_path:
            raise RuntimeError("language_detection_backend='fasttext' requires fasttext_model_path")
        detector = detector_for_backend(
            settings.language_detection_backend, settings.fasttext_model_path
        )
    app.state.language_service = LanguageService(detector=detector)
    # Speech (STT/TTS) seam (D-079): providers load their model weights
    # lazily on first use, so this never fails at startup.
    from app.speech.service import create_speech_service

    app.state.speech_service = create_speech_service(settings)
    if settings.speech_preload:
        # D-079 latency: load STT/TTS weights at startup in the background so
        # the first speech request doesn't pay the model-load cost.
        import threading

        threading.Thread(
            target=lambda: asyncio.run(app.state.speech_service.warm_up()),
            name="speech-warmup",
            daemon=True,
        ).start()

    # Vector-store availability by component (F-029): 1 when the in-memory
    # index is built, 0 when the component failed or is unconfigured.
    VECTOR_DB_UP.set(1 if app.state.retrieval_service is not None else 0, component="statute")
    VECTOR_DB_UP.set(
        1 if app.state.document_retrieval_service is not None else 0, component="documents"
    )

    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(MetricsMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router, prefix=API_V1_PREFIX)
    return app


class MetricsMiddleware:
    """Pure ASGI middleware recording request count and latency (F-025/F-026).

    Wraps ``send`` rather than using ``BaseHTTPMiddleware`` so the request-id
    context variable set by inner middleware stays visible to handlers.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", ""))
        route = str(scope.get("path", ""))
        started = time.perf_counter()
        status = 500

        async def send_with_metrics(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_with_metrics)
        finally:
            REQUESTS.inc(method=method, route=route, status=str(status))
            REQUEST_LATENCY.observe(time.perf_counter() - started, method=method, route=route)


def _build_embedder(settings: Settings) -> EmbeddingProvider:
    """Dense embedder (D-011/D-012); see ``app.ingestion.embeddings.build_embedder``."""
    from app.ingestion.embeddings import build_embedder

    return build_embedder(settings.embedding_backend)


def build_retrieval_service(
    settings: Settings,
    document_retrieval: "DocumentRetrievalService | None" = None,
) -> object | None:
    """Assemble the statute retrieval service when a corpus artifact exists.

    Chat fails closed (503 RETRIEVAL_NOT_CONFIGURED) until the Phase 2
    ingestion artifact is configured via ``RETRIEVAL_CORPUS_PATH``; the
    build never raises so health endpoints stay authoritative.
    """
    if not settings.retrieval_corpus_path:
        return None
    try:
        from app.retrieval.dense import CosineDenseIndex
        from app.retrieval.service import RetrievalService
        from app.retrieval.sparse import Bm25SparseIndex
        from app.retrieval.store import ChunkStore

        store = ChunkStore.from_jsonl(Path(settings.retrieval_corpus_path))
        sparse = Bm25SparseIndex(store.chunks)
        # Persisted corpus vectors (A2-013 one-time embedding): an API
        # restart reloads them instead of re-embedding the whole corpus.
        # "none" disables the cache (semantics unchanged).
        if settings.retrieval_vector_cache_path.lower() == "none":
            cache_path = None
        elif settings.retrieval_vector_cache_path:
            cache_path = Path(settings.retrieval_vector_cache_path)
        else:
            cache_path = Path(settings.storage_dir) / "retrieval_dense_vectors.json"
        dense = CosineDenseIndex(
            store.chunks, _build_embedder(settings), vector_cache_path=cache_path
        )
        return RetrievalService(
            store,
            dense,
            sparse,
            dense_top_k=settings.dense_top_k,
            sparse_top_k=settings.sparse_top_k,
            confidence_threshold=settings.retrieval_confidence_threshold,
            document_confidence_threshold=settings.document_retrieval_confidence_threshold,
            document_retrieval=document_retrieval,
        )
    except Exception:
        logger.warning(
            "retrieval service unavailable",
            extra={"corpus_path": settings.retrieval_corpus_path},
            exc_info=True,
        )
        return None


def _build_documents(
    settings: Settings,
) -> tuple[object | None, object | None]:
    """Assemble the user-document workspace (Phase 5).

    Shares the statute-side embedder (BGE by default, D-011/D-012) so
    uploaded documents are embedded with the same open-weight model.
    """
    try:
        from app.documents.ingestion import DocumentIndex, DocumentWorkspace, _InMemoryDocumentIndex
        from app.documents.retrieval import DocumentRetrievalService
        from app.documents.service import (
            BackgroundJobRunner,
            DocumentService,
            JobRunner,
        )
        from app.documents.storage import DocumentFileStorage
        from app.documents.store import DocumentStore
    except Exception:
        logger.warning("document subsystem unavailable", exc_info=True)
        return None, None

    embedder = _build_embedder(settings)
    runner: JobRunner = BackgroundJobRunner()
    store: DocumentStore
    index: DocumentIndex
    if settings.documents_backend == "redis":
        try:
            import redis as redis_module

            from app.documents.redis_index import RedisDocumentIndex
            from app.documents.redis_store import RedisDocumentStore
            from app.workers.arq_worker import ArqJobRunner

            client = redis_module.Redis.from_url(settings.redis_url, decode_responses=True)
            client.ping()
            store = RedisDocumentStore(client)
            index = RedisDocumentIndex(client)
            workspace = DocumentWorkspace(store, index, embedder)
            runner = ArqJobRunner(settings.redis_url)
        except Exception:
            logger.warning("redis documents backend unavailable; using in-memory", exc_info=True)
            store = DocumentStore()
            index = _InMemoryDocumentIndex()
            workspace = DocumentWorkspace(store, index, embedder)
    else:
        store = DocumentStore()
        index = _InMemoryDocumentIndex()
        workspace = DocumentWorkspace(store, index, embedder)
    service = DocumentService(
        store,
        DocumentFileStorage(Path(settings.storage_dir)),
        workspace,
        runner=runner,
        allowed_types={t.strip() for t in settings.allowed_upload_types.split(",") if t.strip()},
        max_size_bytes=settings.max_upload_size_mb * 1024 * 1024,
    )
    retrieval = DocumentRetrievalService(index, embedder)
    return service, retrieval


def _build_forms_library(settings: Settings) -> object | None:
    """Load the generated forms library when its manifest exists (Phase 6).

    Extraction runs out-of-band (``scripts/extract_forms.py``, idempotent);
    the API fails closed (503 FORMS_NOT_CONFIGURED) until the library is
    generated.
    """
    try:
        from app.forms.library import build_forms_library

        return build_forms_library(settings.forms_output_dir)
    except Exception:
        logger.warning("forms library unavailable", exc_info=True)
        return None


app = create_app()
