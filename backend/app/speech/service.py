"""Speech service facade (D-079).

Wires the STT/TTS provider seams together. Providers are constructed lazily
from configuration so a missing torch/transformers runtime never blocks API
startup; every method can fail closed with a 503 instead. Tests inject fake
providers directly.
"""

from __future__ import annotations

import asyncio
import logging

from app.core.config import Settings
from app.speech.base import (
    EmptyTranscriptionError,
    SpeechProviderError,
    STTProvider,
    TranscriptionResult,
    TTSProvider,
)
from app.speech.indicconformer import mime_supported

logger = logging.getLogger(__name__)


class SpeechService:
    """STT + TTS facade; no chat, retrieval or citation logic lives here."""

    def __init__(
        self,
        *,
        stt: STTProvider | None = None,
        tts: TTSProvider | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._stt = stt
        self._tts = tts
        self._settings = settings

    @property
    def stt(self) -> STTProvider:
        if self._stt is None:
            self._stt = self._build_stt()
        return self._stt

    @property
    def tts(self) -> TTSProvider:
        if self._tts is None:
            self._tts = self._build_tts()
        return self._tts

    def _build_stt(self) -> STTProvider:
        """Provider is selected by SPEECH_STT_PROVIDER (D-079)."""
        settings = self._settings
        if settings is None:
            raise SpeechProviderError("Speech settings are not configured.")
        name = settings.speech_stt_provider
        if name in ("faster-whisper", ""):
            from app.speech.faster_whisper_stt import create_faster_whisper_stt

            return create_faster_whisper_stt(settings)
        if name == "indicconformer":
            from app.speech.indicconformer import create_indicconformer_stt

            return create_indicconformer_stt(settings)
        if name == "whisper":
            from app.speech.whisper_stt import create_whisper_stt

            return create_whisper_stt(settings)
        if name == "openai":
            from app.speech.cloud import create_openai_stt

            return create_openai_stt(settings)
        raise SpeechProviderError(f"Unknown speech STT provider '{name}'.")

    def _build_tts(self) -> TTSProvider:
        settings = self._settings
        if settings is None:
            raise SpeechProviderError("Speech settings are not configured.")
        if settings.speech_tts_provider in ("piper", ""):
            from app.speech.piper_tts import create_piper_tts

            return create_piper_tts(settings)
        if settings.speech_tts_provider == "parler-tts":
            from app.speech.parler_tts import create_parler_tts

            return create_parler_tts(settings)
        if settings.speech_tts_provider == "openai":
            from app.speech.cloud import create_openai_tts

            return create_openai_tts(settings)
        raise SpeechProviderError(f"Unknown speech TTS provider '{settings.speech_tts_provider}'.")

    async def transcribe(
        self, data: bytes, *, mime_type: str | None, language: str | None
    ) -> TranscriptionResult:
        """Transcribe audio; returns detected language + text. No chat send."""
        if not data:
            raise EmptyTranscriptionError("No audio was recorded.")
        if not mime_supported(mime_type):
            from app.core.errors import AppError

            raise AppError(
                "Unsupported audio format. Please record audio in a standard audio format.",
                status_code=415,
                code="AUDIO_FORMAT_UNSUPPORTED",
            )
        result = await self.stt.transcribe(data, mime_type=mime_type or "", language=language)
        if not result.text.strip():
            raise EmptyTranscriptionError("No speech was detected in the audio.")
        return result

    async def synthesize(self, text: str, *, language: str) -> bytes:
        """Synthesize supplied text only. Never retrieves, never alters citations."""
        return (await self.tts.synthesize(text, language=language)).audio

    async def warm_up(self) -> None:
        """Eagerly load provider weights (SPEECH_PRELOAD, D-079 latency).

        Best-effort: failures are logged and swallowed — requests still lazy
        load (or fail closed 503) exactly as before.
        """
        for name in ("stt", "tts"):
            try:
                provider = getattr(self, name)
                warm = getattr(provider, "warm_up", None)
                if warm is None:
                    continue
                await asyncio.to_thread(warm)
                logger.info("speech provider warmed up", extra={"provider": name})
            except Exception:
                logger.warning(
                    "speech provider warm-up failed (will lazy-load on first use)",
                    extra={"provider": name},
                )


def create_speech_service(settings: Settings) -> SpeechService:
    return SpeechService(settings=settings)


__all__ = ["SpeechService", "create_speech_service"]
