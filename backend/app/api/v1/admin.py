"""Admin API: settings console, status, connection tests, corpus management.

All routes live under ``/api/v1/admin`` and require an env-configured admin
account (ADMIN_USERNAME / ADMIN_PASSWORD) plus a valid signed session cookie
(DECISIONS.md D-080). Secrets are masked in every response and never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from pydantic import BaseModel, Field

from app.admin import auth
from app.admin.corpus import CorpusReplacementError, build_replacement, verify_artifact
from app.admin.store import EDITABLE_FIELDS, SECRET_FIELDS, AdminSettingsStore, mask_secret
from app.core.config import Settings
from app.core.errors import AppError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# --- helpers -------------------------------------------------------------------


def _store(request: Request) -> AdminSettingsStore:
    return request.app.state.admin_store  # type: ignore[no-any-return]


def _admin_required(request: Request) -> None:
    auth.require_admin(request, mutating=False)


def _admin_required_mutating(request: Request) -> None:
    auth.require_admin(request, mutating=True)


AdminDep = Annotated[None, Depends(_admin_required)]
AdminMutatingDep = Annotated[None, Depends(_admin_required_mutating)]


def _settings_view(settings: Settings, store: AdminSettingsStore) -> dict[str, Any]:
    """Public (masked) view of the effective settings + which fields are editable."""
    persisted = store.load()
    values: dict[str, Any] = {}
    for key in sorted(EDITABLE_FIELDS):
        values[key] = getattr(settings, key)
    secrets = {key: mask_secret(getattr(settings, key, None)) for key in sorted(SECRET_FIELDS)}
    return {
        "values": values,
        "secrets": secrets,  # "set" | "" — never the value
        "persisted": sorted(persisted["settings"]),
        "llm_providers": request_llm_providers(),
    }


def request_llm_providers() -> list[dict[str, Any]]:
    from app.llm.openai_compat import PROFILES

    providers = [{"name": "ollama", "label": "Ollama (local, keyless)", "requires_api_key": False}]
    for name, (url, _default_model, label) in sorted(PROFILES.items()):
        providers.append(
            {"name": name, "label": label, "requires_api_key": True, "default_base_url": url}
        )
    providers.append({"name": "gemini", "label": "Google Gemini", "requires_api_key": True})
    return providers


def _apply_settings(request: Request, new_settings: Settings) -> None:
    """Swap effective settings and rebuild the services that cache them.

    The LLM provider is resolved per request (registry), so only the speech
    service and the retrieval service (top-k / thresholds / corpus path) need
    an explicit rebuild.
    """
    from app.speech.service import create_speech_service

    request.app.state.settings = new_settings
    request.app.state.speech_service = create_speech_service(new_settings)
    retrieval = _build_retrieval(new_settings, request)
    request.app.state.retrieval_service = retrieval


def _build_retrieval(settings: Settings, request: Request) -> object | None:
    from app.main import build_retrieval_service

    return build_retrieval_service(
        settings, document_retrieval=getattr(request.app.state, "document_retrieval_service", None)
    )


# --- auth ----------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


@router.post("/login")
async def login(body: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    settings = request.app.state.settings
    if not auth.admin_enabled(settings):
        raise AppError(
            "The admin console is disabled. Set ADMIN_USERNAME and ADMIN_PASSWORD.",
            status_code=503,
            code="ADMIN_DISABLED",
        )
    if not auth.verify_credentials(settings, body.username, body.password):
        raise AppError("Invalid admin credentials.", status_code=401, code="ADMIN_UNAUTHORIZED")
    auth.start_session(response, settings)
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response) -> dict[str, Any]:
    auth.end_session(response)
    return {"ok": True}


@router.get("/session")
async def session_status(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    if not auth.admin_enabled(settings):
        return {"enabled": False, "authenticated": False}
    try:
        auth.require_admin(request, mutating=False)
        return {"enabled": True, "authenticated": True}
    except AppError:
        return {"enabled": True, "authenticated": False}


# --- settings ------------------------------------------------------------------


@router.get("/settings")
async def get_settings_view(request: Request, _: AdminDep) -> dict[str, Any]:
    return _settings_view(request.app.state.settings, _store(request))


class UpdateSettingsRequest(BaseModel):
    """Field-by-field partial update; unknown keys are rejected."""

    values: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


@router.put("/settings")
async def update_settings(
    body: UpdateSettingsRequest, request: Request, _: AdminMutatingDep
) -> dict[str, Any]:
    store = _store(request)
    current: Settings = request.app.state.settings

    unknown = (set(body.values) | set(body.secrets)) - EDITABLE_FIELDS - SECRET_FIELDS
    if unknown:
        raise AppError(
            f"Unknown or read-only settings: {', '.join(sorted(unknown))}.",
            status_code=422,
            code="SETTINGS_INVALID",
        )
    # Empty secret strings mean "unchanged" (the UI never echoes values back).
    secret_updates = {k: v for k, v in body.secrets.items() if v}

    persisted = store.load()
    merged_values = {**persisted["settings"], **body.values}
    merged_secrets = {**persisted["secrets"], **secret_updates}

    base_dump = current.model_dump()
    merged: dict[str, Any] = {**base_dump, **merged_values}
    for key in SECRET_FIELDS:
        # Environment-provided secrets are never overridden by the console;
        # otherwise the persisted secret applies.
        if base_dump.get(key) is None and merged_secrets.get(key):
            merged[key] = merged_secrets[key]
        else:
            merged[key] = base_dump.get(key)

    try:
        candidate = Settings(**merged)
    except Exception as exc:
        raise AppError(
            "The submitted configuration is invalid.",
            status_code=422,
            code="SETTINGS_INVALID",
        ) from exc

    # Validate provider selection against the registry before applying.
    registry = request.app.state.llm_registry
    if candidate.llm_provider not in registry.available():
        raise AppError(
            f"Unknown LLM provider '{candidate.llm_provider}'.",
            status_code=422,
            code="SETTINGS_INVALID",
        )

    store.save(merged_values, merged_secrets, persisted.get("corpus") or {})
    _apply_settings(request, candidate)
    logger.info("admin settings updated", extra={"fields": sorted(body.values)})
    return _settings_view(candidate, store)


# --- connection tests ------------------------------------------------------------


class TestResult(BaseModel):
    success: bool
    latency_ms: int | None = None
    message: str


@router.post("/test/llm")
async def test_llm(request: Request, _: AdminDep) -> TestResult:
    from app.api.deps import get_llm_provider

    start = time.monotonic()
    try:
        provider = get_llm_provider(request)
        healthy = await provider.health_check()
        latency = int((time.monotonic() - start) * 1000)
        meta = provider.metadata()
        return TestResult(
            success=healthy,
            latency_ms=latency,
            message=(
                f"{meta.provider} / {meta.model}: reachable."
                if healthy
                else f"{meta.provider} / {meta.model}: unreachable or not configured."
            ),
        )
    except AppError as exc:
        return TestResult(success=False, message=exc.message)
    except Exception:
        return TestResult(success=False, message="Provider test failed unexpectedly.")


def _silence_wav() -> bytes:
    """Tiny valid WAV (0.2 s, 16 kHz, silence + blip) for STT reachability."""
    import io
    import struct
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        frames = b""
        for i in range(3200):  # 0.2 s
            value = 8000 if 1000 < i < 1100 else 0
            frames += struct.pack("<h", value)
        handle.writeframes(frames)
    return buffer.getvalue()


@router.post("/test/stt")
async def test_stt(request: Request, _: AdminDep) -> TestResult:
    service = getattr(request.app.state, "speech_service", None)
    if service is None:
        return TestResult(success=False, message="Speech service is not configured.")
    start = time.monotonic()
    try:
        await service.transcribe(_silence_wav(), mime_type="audio/wav", language=None)
    except AppError as exc:
        # Provider reached and executed but found no speech — still proves the
        # pipeline works. Anything else (503/decode) is a real failure.
        if exc.code == "EMPTY_TRANSCRIPTION":
            return TestResult(
                success=True,
                latency_ms=int((time.monotonic() - start) * 1000),
                message="STT provider loaded and responded (no speech in test clip).",
            )
        return TestResult(success=False, message=exc.message)
    except Exception:
        return TestResult(success=False, message="STT test failed unexpectedly.")
    return TestResult(  # pragma: no cover - transcription of silence is empty
        success=True,
        latency_ms=int((time.monotonic() - start) * 1000),
        message="STT provider responded.",
    )


@router.post("/test/tts")
async def test_tts(request: Request, _: AdminDep) -> TestResult:
    service = getattr(request.app.state, "speech_service", None)
    if service is None:
        return TestResult(success=False, message="Speech service is not configured.")
    start = time.monotonic()
    try:
        audio = await service.synthesize("Test.", language="en")
    except AppError as exc:
        return TestResult(success=False, message=exc.message)
    except Exception:
        return TestResult(success=False, message="TTS test failed unexpectedly.")
    if not audio:
        return TestResult(success=False, message="TTS provider returned no audio.")
    return TestResult(
        success=True,
        latency_ms=int((time.monotonic() - start) * 1000),
        message=f"TTS provider returned {len(audio)} bytes of audio.",
    )


# --- system status ----------------------------------------------------------------


@router.get("/status")
async def system_status(request: Request, _: AdminDep) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    checks = await asyncio.gather(
        _check_postgres(settings),
        _check_redis(settings),
        _check_qdrant(settings),
    )
    postgres, redis, qdrant = checks
    return {
        "backend": {"status": "ok", "version": _app_version()},
        "postgres": postgres,
        "redis": redis,
        "qdrant": qdrant,
        "llm": await _llm_status(request),
        "stt": _speech_status(settings, "stt"),
        "tts": _speech_status(settings, "tts"),
        "corpus": _corpus_status(request),
        "worker": await _worker_status(settings),
    }


def _app_version() -> str:
    from app.core.config import APP_VERSION

    return APP_VERSION


async def _tcp_status(name: str, url: str) -> dict[str, str]:
    """Truthful dependency probe: ok / error with the dependency's message."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.get(url)
        if response.status_code < 500:
            return {"status": "ok", "detail": f"HTTP {response.status_code}"}
        return {"status": "error", "detail": f"HTTP {response.status_code}"}
    except httpx.HTTPError:
        return {"status": "unavailable", "detail": "unreachable"}


async def _check_postgres(settings: Settings) -> dict[str, str]:
    try:
        import asyncpg  # type: ignore[import-not-found, import-untyped]

        # DATABASE_URL is SQLAlchemy-style ("postgresql+asyncpg://...");
        # asyncpg wants the bare postgresql scheme.
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=4)
        await conn.close()
        return {"status": "ok", "detail": "connected"}
    except Exception:
        return {"status": "unavailable", "detail": "unreachable"}


async def _check_redis(settings: Settings) -> dict[str, str]:
    try:
        import redis.asyncio as aioredis  # type: ignore[import-not-found, import-untyped]

        client = aioredis.from_url(settings.redis_url)  # type: ignore[no-untyped-call]
        await asyncio.wait_for(client.ping(), timeout=4)
        await client.aclose()
        return {"status": "ok", "detail": "connected"}
    except Exception:
        return {"status": "unavailable", "detail": "unreachable"}


async def _check_qdrant(settings: Settings) -> dict[str, str]:
    return await _tcp_status("qdrant", settings.qdrant_url.rstrip("/") + "/healthz")


async def _llm_status(request: Request) -> dict[str, Any]:
    from app.api.deps import get_llm_provider

    try:
        provider = get_llm_provider(request)
        meta = provider.metadata()
        healthy = await provider.health_check()
        return {
            "status": "ok" if healthy else "error",
            "provider": meta.provider,
            "model": meta.model,
            "detail": "reachable" if healthy else "unreachable",
        }
    except AppError as exc:
        return {"status": "not_configured", "provider": None, "model": None, "detail": exc.message}


def _speech_status(settings: Settings, which: str) -> dict[str, Any]:
    provider = settings.speech_stt_provider if which == "stt" else settings.speech_tts_provider
    model = settings.speech_stt_model if which == "stt" else settings.speech_tts_model
    device = settings.speech_stt_device if which == "stt" else settings.speech_tts_device
    return {
        "status": "configured",
        "provider": provider,
        "model": model,
        "device": device,
        "detail": "configured; use the test button to verify the model loads",
    }


def _corpus_status(request: Request) -> dict[str, Any]:
    manifest = _store(request).load().get("corpus") or {}
    service = getattr(request.app.state, "retrieval_service", None)
    if service is None:
        return {"status": "not_configured", "detail": "no active retrieval service"}
    if manifest:
        return {"status": "ok", "detail": "active", **manifest}
    settings: Settings = request.app.state.settings
    return {
        "status": "ok",
        "detail": "active (environment-configured corpus)",
        "artifact_path": settings.retrieval_corpus_path,
    }


async def _worker_status(settings: Settings) -> dict[str, Any]:
    if settings.documents_backend != "redis":
        return {
            "status": "not_configured",
            "detail": "documents backend is 'memory'; no worker required",
        }
    try:
        import redis.asyncio as aioredis  # type: ignore[import-not-found, import-untyped]

        client = aioredis.from_url(settings.redis_url)  # type: ignore[no-untyped-call]
        queued = await asyncio.wait_for(client.llen("arq:queue"), timeout=4)
        await client.aclose()
        return {"status": "ok", "detail": f"queue depth {queued}"}
    except Exception:
        return {"status": "unavailable", "detail": "redis unreachable"}


# --- corpus management ------------------------------------------------------------


@router.get("/corpus")
async def corpus_info(request: Request, _: AdminDep) -> dict[str, Any]:
    return _corpus_status(request)


@router.post("/corpus")
async def replace_corpus(
    request: Request, _: AdminMutatingDep, file: Annotated[UploadFile, File()]
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    max_bytes = settings.max_upload_size_mb * 1024 * 1024

    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type != "application/pdf":
        raise AppError(
            "Only PDF uploads are accepted.",
            status_code=415,
            code="AUDIO_FORMAT_UNSUPPORTED",
        )
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise AppError(
            "The uploaded file exceeds the size limit.",
            status_code=413,
            code="DOCUMENT_TOO_LARGE",
        )
    if not data:
        raise AppError("The uploaded file is empty.", status_code=422, code="DOCUMENT_INVALID")

    artifacts_dir = Path(settings.storage_dir) / "admin-corpus"
    try:
        manifest, artifact_path = build_replacement(data, settings, artifacts_dir=artifacts_dir)
    except CorpusReplacementError as exc:
        logger.warning("corpus replacement rejected", extra={"reason": str(exc)[:200]})
        raise AppError(str(exc), status_code=422, code="CORPUS_REJECTED") from exc

    # Build the replacement service against the new artifact.
    candidate = settings.model_copy(update={"retrieval_corpus_path": str(artifact_path)})
    new_service = _build_retrieval(candidate, request)
    if new_service is None:
        _safe_unlink(artifact_path)
        raise AppError(
            "The new corpus could not be indexed; the active corpus is unchanged.",
            status_code=500,
            code="CORPUS_ACTIVATION_FAILED",
        )
    try:
        verify_artifact(new_service)
    except CorpusReplacementError as exc:
        _safe_unlink(artifact_path)
        raise AppError(str(exc), status_code=500, code="CORPUS_ACTIVATION_FAILED") from exc

    # Atomic activation: state swap + persisted manifest.
    store = _store(request)
    persisted = store.load()
    store.save(persisted["settings"], persisted["secrets"], manifest)
    request.app.state.retrieval_service = new_service
    request.app.state.settings = candidate
    logger.info(
        "corpus activated",
        extra={"sha256": manifest["sha256"], "chunks": manifest["chunks"]},
    )
    return {"status": "ok", "corpus": manifest}


def _safe_unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


# --- memory -----------------------------------------------------------------------


@router.get("/memory")
async def memory_info(request: Request, _: AdminDep) -> dict[str, Any]:
    """What memory actually is here: client-side conversation history,
    capped server-side; never a source of legal authority (D-080)."""
    settings: Settings = request.app.state.settings
    return {
        "architecture": "client-side conversation history sent per request",
        "history_max_turns": settings.chat_history_max_turns,
        "history_untrusted": True,
        "persistent_server_memory": False,
    }


class UpdateMemoryRequest(BaseModel):
    chat_history_max_turns: int = Field(ge=1, le=50)


@router.put("/memory")
async def update_memory(
    body: UpdateMemoryRequest, request: Request, _: AdminMutatingDep
) -> dict[str, Any]:
    store = _store(request)
    persisted = store.load()
    settings_values = {
        **persisted["settings"],
        "chat_history_max_turns": body.chat_history_max_turns,
    }
    store.save(settings_values, persisted["secrets"], persisted.get("corpus") or {})
    current: Settings = request.app.state.settings
    _apply_settings(
        request, current.model_copy(update={"chat_history_max_turns": body.chat_history_max_turns})
    )
    return {
        "chat_history_max_turns": body.chat_history_max_turns,
        "note": "Existing conversations live in the browser; clearing them is a client action.",
    }


__all__ = ["router"]
