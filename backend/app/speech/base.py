"""Speech (STT/TTS) provider contracts and errors.

Voice is an input/output layer only (D-079): transcription feeds the existing
multilingual chat composer, synthesis reads back an already-generated answer.
The speech layer never performs retrieval, generation or citation handling,
and never inspects or mutates chat/RAG behaviour.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from app.core.errors import AppError


class SpeechProviderError(AppError):
    """The configured speech provider failed or is unavailable."""

    status_code = 503
    code = "SPEECH_PROVIDER_UNAVAILABLE"


class SpeechProviderNotConfiguredError(SpeechProviderError):
    """The configured speech provider cannot be constructed."""

    code = "SPEECH_PROVIDER_NOT_CONFIGURED"


class SpeechUnsupportedLanguageError(AppError):
    """The requested language has no speech support (never a silent fallback)."""

    status_code = 400
    code = "SPEECH_LANGUAGE_UNSUPPORTED"


class AudioDecodeError(AppError):
    """The uploaded audio could not be decoded."""

    status_code = 400
    code = "AUDIO_DECODE_FAILED"


class EmptyTranscriptionError(AppError):
    """The provider returned no usable transcription."""

    status_code = 422
    code = "EMPTY_TRANSCRIPTION"


class TranscriptionResult(BaseModel):
    """Provider-agnostic transcription output."""

    text: str
    #: Raw provider language code ("hi", "eng", ...).
    language: str


class SynthesisResult(BaseModel):
    """Provider-agnostic synthesis output."""

    audio: bytes
    media_type: str


@runtime_checkable
class STTProvider(Protocol):
    """Speech-to-text provider seam (swappable via SPEECH_STT_PROVIDER)."""

    async def transcribe(
        self, data: bytes, *, mime_type: str, language: str | None
    ) -> TranscriptionResult: ...


@runtime_checkable
class TTSProvider(Protocol):
    """Text-to-speech provider seam (swappable via SPEECH_TTS_PROVIDER)."""

    async def synthesize(self, text: str, *, language: str) -> SynthesisResult: ...


__all__ = [
    "AudioDecodeError",
    "EmptyTranscriptionError",
    "STTProvider",
    "SpeechProviderError",
    "SpeechProviderNotConfiguredError",
    "SpeechUnsupportedLanguageError",
    "SynthesisResult",
    "TTSProvider",
    "TranscriptionResult",
]
