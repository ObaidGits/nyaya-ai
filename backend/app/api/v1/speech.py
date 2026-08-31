"""Speech API endpoints (STT/TTS, DECISIONS.md D-079).

Voice is an input/output layer only:

* ``POST /speech/transcribe`` validates and transcribes uploaded audio and
  returns text + detected language. It never sends the text to chat — the
  client inserts it into the composer for user review (assignment rule).
* ``POST /speech/synthesize`` synthesizes the supplied assistant text in the
  supplied language. It never retrieves, generates, or alters citations.

Both endpoints honour session identity, rate limits, and the standard error
envelope, and never leak internal paths or tracebacks.
"""

from __future__ import annotations

import re
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.core.errors import AppError
from app.core.rate_limit import SPEECH_SCOPE, enforce_rate_limit
from app.language.models import LANGUAGE_PREFERENCES
from app.speech.service import SpeechService

router = APIRouter(prefix="/speech", tags=["speech"])

SESSION_HEADER = "X-Session-Id"
_SAFE_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class SessionMissingError(AppError):
    """The request carries no usable session identity (same rule as D-040)."""

    status_code = 400
    code = "SESSION_REQUIRED"


def get_session_id(
    x_session_id: Annotated[str | None, Header(alias="X-Session-Id")] = None,
) -> str:
    if x_session_id is None or not _SAFE_SESSION_RE.fullmatch(x_session_id):
        raise SessionMissingError("A valid X-Session-Id header is required.")
    return x_session_id


def get_speech_service(request: Request) -> SpeechService:
    service = cast(SpeechService | None, getattr(request.app.state, "speech_service", None))
    if service is None:
        raise AppError(
            "Speech features are not configured on this instance.",
            status_code=503,
            code="SPEECH_NOT_CONFIGURED",
        )
    return service


class TranscribeResponse(JSONResponse):
    """Transcription result: text only; the client decides what to do with it."""

    def __init__(self, payload: dict[str, object]) -> None:
        super().__init__(status_code=200, content=payload)


def _enforce_speech_limit(request: Request, session_id: str) -> None:
    limiter = getattr(request.app.state, "rate_limiter", None)
    settings = getattr(request.app.state, "settings", None)
    if limiter is None or settings is None:
        return
    enforce_rate_limit(
        limiter,
        scope=SPEECH_SCOPE,
        key=session_id,
        limit=settings.rate_limit_speech_per_minute,
        window_seconds=60.0,
    )


async def _read_audio(file: UploadFile, max_bytes: int) -> bytes:
    """Read the audio body in bounded chunks; reject early when oversized."""
    buffer = bytearray()
    while True:
        chunk = await file.read(_CHUNK_BYTES)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise AppError(
                f"The uploaded audio exceeds the maximum size of {max_bytes // (1024 * 1024)} MB.",
                status_code=413,
                code="AUDIO_TOO_LARGE",
            )
    return bytes(buffer)


_CHUNK_BYTES = 1024 * 1024


def _validate_language(language: str | None) -> str | None:
    """Accept 'auto' or a supported code; anything else is a 422."""
    if not language:
        return None
    if language not in LANGUAGE_PREFERENCES:
        raise AppError(
            f"Unsupported language '{language}'.",
            status_code=422,
            code="SPEECH_LANGUAGE_INVALID",
        )
    return None if language == "auto" else language


@router.get("/config")
async def speech_config(request: Request) -> dict[str, str]:
    """Non-secret speech provider selection so the client can route.

    ``browser`` providers are executed client-side (Web Speech API /
    speechSynthesis) so a resource-constrained server holds no speech
    models; the server endpoints then fail closed if reached anyway.
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise AppError(
            "Speech features are not configured on this instance.",
            status_code=503,
            code="SPEECH_NOT_CONFIGURED",
        )
    return {
        "stt_provider": settings.speech_stt_provider,
        "tts_provider": settings.speech_tts_provider,
    }


@router.post("/transcribe")
async def transcribe_audio(
    request: Request,
    file: UploadFile,
    session_id: Annotated[str, Depends(get_session_id)],
    service: Annotated[SpeechService, Depends(get_speech_service)],
    language: Annotated[str | None, Query()] = None,
) -> TranscribeResponse:
    """Transcribe one audio clip; returns text + detected language (D-079).

    The transcription is returned to the client only — it is never sent to
    chat automatically, so the user always reviews/edits before submission.
    """
    _enforce_speech_limit(request, session_id)
    settings = getattr(request.app.state, "settings", None)
    max_bytes = (
        settings.max_audio_upload_mb * 1024 * 1024 if settings is not None else 15 * 1024 * 1024
    )
    data = await _read_audio(file, max_bytes)
    requested = _validate_language(language)
    result = await service.transcribe(data, mime_type=file.content_type, language=requested)
    return TranscribeResponse({"text": result.text, "language": result.language})


class SynthesizeRequest(BaseModel):
    """Assistant text to read back plus the answer's language."""

    text: str = Field(min_length=1, max_length=5000)
    language: str = Field(min_length=2, max_length=8)


@router.post("/synthesize")
async def synthesize_speech(
    request: Request,
    payload: SynthesizeRequest,
    session_id: Annotated[str, Depends(get_session_id)],
    service: Annotated[SpeechService, Depends(get_speech_service)],
) -> Response:
    """Synthesize the supplied text in the supplied language (D-079).

    Only the supplied text is synthesized — no retrieval, no generation, no
    citation mutation. Unsupported languages fail clearly, never fall back
    to another language silently.
    """
    _enforce_speech_limit(request, session_id)
    language = payload.language
    if language not in LANGUAGE_PREFERENCES or language == "auto":
        raise AppError(
            f"Speech synthesis does not support language '{language}'.",
            status_code=422,
            code="SPEECH_LANGUAGE_INVALID",
        )
    audio = await service.synthesize(payload.text, language=language)
    return Response(
        content=audio,
        media_type="audio/wav",
        headers={"Content-Disposition": "inline", "Cache-Control": "no-store"},
    )
