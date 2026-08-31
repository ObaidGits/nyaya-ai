"""OpenAI Whisper STT provider (local, public weights; D-079 alternative).

Real local transcription over ``openai/whisper-*`` via transformers. This is
the fallback STT backend used when the preferred IndicConformer weights are
unavailable (they are license-gated on the Hub); Whisper natively detects
the spoken language. Same lazy-load + device contract as IndicConformer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.config import Settings
from app.speech.base import SpeechProviderError, TranscriptionResult
from app.speech.decode import decode_to_wav_pcm16k_mono

logger = logging.getLogger(__name__)

# Whisper language codes align with our two-letter codes for the 12 languages.
_SUPPORTED = {"en", "hi", "bn", "mr", "gu", "ta", "te", "kn", "ml", "pa", "or", "as"}

# Shared decode (soundfile fast path + ffmpeg fallback for browser WebM/Opus).
_decode_audio = decode_to_wav_pcm16k_mono


class WhisperSTT:
    """Lazy-loading local STT provider backed by Whisper."""

    def __init__(self, model_name: str, *, device: str = "auto") -> None:
        self.model_name = model_name
        self.device_setting = device
        self._pipeline: Any = None

    def _load(self) -> None:
        if self._pipeline is not None:
            return
        try:
            import torch
            from transformers import pipeline  # type: ignore[import-untyped]

            device = self.device_setting
            if device == "auto":
                device = "cuda" if torch.cuda.is_available() else "cpu"
            self._pipeline = pipeline(
                "automatic-speech-recognition",
                model=self.model_name,
                device=device,
            )
        except Exception as exc:
            logger.warning(
                "whisper stt unavailable",
                extra={"model": self.model_name, "error_type": type(exc).__name__},
            )
            self._pipeline = None
            raise SpeechProviderError(
                "The speech-to-text provider is not available on this instance."
            ) from exc

    def warm_up(self) -> None:
        """Eagerly load weights (startup pre-load, D-079 latency)."""
        self._load()

    def _run(self, audio: Any, language: str | None) -> tuple[str, str]:
        # transformers 4.4x ASR pipeline takes the forced language through
        # generate_kwargs; `return_language` reports the detected language.
        kwargs: dict[str, Any] = {"return_language": True}
        if language:
            kwargs["generate_kwargs"] = {"language": language}
        result = self._pipeline(audio.numpy(), **kwargs)
        text = str(result.get("text", "")).strip()
        detected = str(result.get("language") or language or "").strip() or "en"
        return text, detected

    async def transcribe(
        self, data: bytes, *, mime_type: str, language: str | None
    ) -> TranscriptionResult:
        if language and language not in _SUPPORTED:
            from app.speech.base import SpeechUnsupportedLanguageError

            raise SpeechUnsupportedLanguageError(
                f"Speech recognition does not support language '{language}'."
            )
        self._load()
        audio = _decode_audio(data)
        try:
            text, detected = await asyncio.to_thread(self._run, audio, language)
        except SpeechProviderError:
            raise
        except Exception:
            logger.warning("whisper transcription failed", exc_info=True)
            raise SpeechProviderError("Transcription failed. Please try again.") from None
        return TranscriptionResult(text=text, language=detected)


def create_whisper_stt(settings: Settings) -> WhisperSTT:
    return WhisperSTT(settings.speech_stt_model, device=settings.speech_stt_device)


__all__ = ["WhisperSTT", "create_whisper_stt"]
