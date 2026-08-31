"""faster-whisper STT provider (CTranslate2; default local backend, D-079).

~4x faster than the transformers Whisper pipeline on CPU thanks to int8
quantization, ~500 MB RAM for ``small`` — the best speed/multilingual/footprint
trade-off for low-spec deployment. Weights are public (no Hub gating), the
model loads lazily, and the device/compute type follow settings.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.config import Settings
from app.speech.base import SpeechProviderError, TranscriptionResult
from app.speech.decode import decode_to_wav_pcm16k_mono

logger = logging.getLogger(__name__)

# faster-whisper speaks the same two-letter codes we use for the 12 languages.
_SUPPORTED = {"en", "hi", "bn", "mr", "gu", "ta", "te", "kn", "ml", "pa", "or", "as"}


class FasterWhisperSTT:
    """Lazy-loading local STT provider backed by CTranslate2 Whisper."""

    def __init__(self, model_name: str, *, device: str = "auto") -> None:
        self.model_name = model_name
        self.device_setting = device
        self._model: Any = None

    def _resolve(self) -> tuple[str, str]:
        """Map settings to (device, compute_type) — int8 keeps RAM tiny."""
        if self.device_setting == "cuda":
            return "cuda", "float16"
        if self.device_setting == "cpu":
            return "cpu", "int8"
        # auto: GPU when visible, else int8 CPU.
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda", "float16"
        except Exception:  # torch missing here is fine: cpu fallback
            pass
        return "cpu", "int8"

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]

            device, compute_type = self._resolve()
            self._model = WhisperModel(self.model_name, device=device, compute_type=compute_type)
        except Exception as exc:
            logger.warning(
                "faster-whisper stt unavailable",
                extra={"model": self.model_name, "error_type": type(exc).__name__},
            )
            self._model = None
            raise SpeechProviderError(
                "The speech-to-text provider is not available on this instance."
            ) from exc

    def warm_up(self) -> None:
        """Eagerly load weights (startup pre-load, D-079 latency)."""
        self._load()

    def _run(self, audio: Any, language: str | None) -> tuple[str, str]:
        segments, info = self._model.transcribe(audio.numpy(), language=language, vad_filter=True)
        text = " ".join(str(segment.text).strip() for segment in segments).strip()
        detected = str(getattr(info, "language", "") or language or "").strip() or "en"
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
        audio = decode_to_wav_pcm16k_mono(data)
        try:
            text, detected = await asyncio.to_thread(self._run, audio, language)
        except SpeechProviderError:
            raise
        except Exception:
            logger.warning("faster-whisper transcription failed", exc_info=True)
            raise SpeechProviderError("Transcription failed. Please try again.") from None
        return TranscriptionResult(text=text, language=detected)


def create_faster_whisper_stt(settings: Settings) -> FasterWhisperSTT:
    return FasterWhisperSTT(settings.speech_stt_model, device=settings.speech_stt_device)


__all__ = ["FasterWhisperSTT", "create_faster_whisper_stt"]
