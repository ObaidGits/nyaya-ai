"""AI4Bharat IndicConformer STT provider (default STT backend, D-079).

Real local transcription over ``ai4bharat/indic-conformer-600m-multilingual`` via
transformers. The model is heavy (~2.4 GB) so it is loaded lazily on first
use and cached on the provider instance; the device is configurable
(``SPEECH_STT_DEVICE`` = auto|cuda|cpu) because the model must share a 6 GB
GPU with the LLM. Auto-detection runs the adapter set configured by
``SPEECH_STT_AUTO_LANGUAGES`` and keeps the highest mean log-probability
candidate — a real acoustic choice, not a heuristic.

Dependencies (torch, transformers) are imported inside methods: the API boots
and fails closed (503 SPEECH_PROVIDER_UNAVAILABLE) without them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.config import Settings
from app.speech.base import SpeechProviderError, TranscriptionResult

logger = logging.getLogger(__name__)

#: Our two-letter codes -> IndicConformer adapter codes (ISO 639-3 where used).
_ADAPTER_CODES: dict[str, str] = {
    "en": "eng",
    "hi": "hin",
    "bn": "ben",
    "mr": "mar",
    "gu": "guj",
    "ta": "tam",
    "te": "tel",
    "kn": "kan",
    "ml": "mal",
    "pa": "pan",
    "or": "ori",
    "as": "asm",
}

#: Adapter codes -> our two-letter codes.
_ADAPTER_TO_CODE = {v: k for k, v in _ADAPTER_CODES.items()}

# Supported MIME types (prefix match; parameters after ";" are ignored).
SUPPORTED_MIME_PREFIXES = (
    "audio/webm",
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/flac",
    "audio/x-flac",
)


def mime_supported(mime_type: str | None) -> bool:
    """True when the MIME type is an accepted audio container."""
    if not mime_type:
        return False
    base = mime_type.split(";", 1)[0].strip().lower()
    return base in SUPPORTED_MIME_PREFIXES


def _normalize_language(raw: str) -> str:
    """Map a provider/adapt language code to our two-letter code when known."""
    lowered = raw.strip().lower()
    if lowered in _ADAPTER_TO_CODE:
        return _ADAPTER_TO_CODE[lowered]
    return lowered


class IndicConformerSTT:
    """Lazy-loading local STT provider backed by IndicConformer 600M."""

    def __init__(
        self,
        model_name: str,
        *,
        device: str = "auto",
        auto_languages: list[str] | None = None,
    ) -> None:
        self.model_name = model_name
        self.device_setting = device
        self.auto_languages = auto_languages or ["en", "hi", "bn", "ta"]
        self._model: Any = None
        self._processor: Any = None
        self._loaded_adapter: str | None = None
        self._device: str = "cpu"

    # --- lazy runtime -------------------------------------------------------

    def _resolve_device(self, torch_module: Any) -> str:
        if self.device_setting != "auto":
            return self.device_setting
        return "cuda" if torch_module.cuda.is_available() else "cpu"

    def _load(self) -> None:
        """Import dependencies and load the model on first use."""
        if self._model is not None:
            return
        try:
            import torch
            from transformers import (  # type: ignore[import-untyped]
                AutoModelForCTC,
                AutoProcessor,
            )

            device = self._resolve_device(torch)
            self._processor = AutoProcessor.from_pretrained(self.model_name)  # type: ignore[no-untyped-call]
            self._model = AutoModelForCTC.from_pretrained(self.model_name).to(device)
            self._model.eval()
            self._device = device
        except Exception as exc:  # missing deps, weights, CUDA — all 503
            logger.warning(
                "indicconformer unavailable",
                extra={"model": self.model_name, "error_type": type(exc).__name__},
            )
            self._model = None
            raise SpeechProviderError(
                "The speech-to-text provider is not available on this instance."
            ) from exc

    def warm_up(self) -> None:
        """Eagerly load weights (startup pre-load, D-079 latency)."""
        self._load()

    def _ensure_adapter(self, adapter: str) -> None:
        if self._loaded_adapter == adapter:
            return
        self._model.load_adapter(adapter)
        self._loaded_adapter = adapter

    def _decode_audio(self, data: bytes) -> Any:
        """Decode raw audio bytes to a float tensor at 16 kHz (ffmpeg fallback
        covers browser WebM/Opus, which libsndfile cannot read)."""
        from app.speech.decode import decode_to_wav_pcm16k_mono

        return decode_to_wav_pcm16k_mono(data)

    def _transcribe_with(self, waveform: Any, adapter: str) -> tuple[str, float]:
        """Run CTC for one language adapter; return (text, mean log-prob)."""
        import torch

        self._ensure_adapter(adapter)
        inputs = self._processor(waveform.squeeze(0), sampling_rate=16000, return_tensors="pt")
        device = self._device
        input_values = inputs.input_values.to(device)
        with torch.no_grad():
            logits = self._model(input_values).logits
            log_probs = torch.log_softmax(logits, dim=-1).mean()
            predicted = torch.argmax(logits, dim=-1)
        text = str(self._processor.batch_decode(predicted)[0]).strip()
        return text, float(log_probs.item())

    # --- STTProvider --------------------------------------------------------

    async def transcribe(
        self, data: bytes, *, mime_type: str, language: str | None
    ) -> TranscriptionResult:
        self._load()
        waveform = self._decode_audio(data)
        requested = None
        if language and language != "auto":
            requested = _ADAPTER_CODES.get(language)
            if requested is None:
                from app.speech.base import SpeechUnsupportedLanguageError

                raise SpeechUnsupportedLanguageError(
                    f"Speech recognition does not support language '{language}'."
                )
        if requested is not None:
            text, _ = await asyncio.to_thread(self._transcribe_with, waveform, requested)
            return TranscriptionResult(text=text, language=_normalize_language(requested))
        # Auto-detect: score each candidate adapter acoustically.
        best_text, best_lang, best_score = "", "", float("-inf")
        for candidate in self.auto_languages:
            adapter = _ADAPTER_CODES.get(candidate)
            if adapter is None:
                continue
            text, score = await asyncio.to_thread(self._transcribe_with, waveform, adapter)
            if score > best_score:
                best_text, best_lang, best_score = text, adapter, score
        if not best_lang:
            raise SpeechProviderError("No speech language candidates are configured.")
        return TranscriptionResult(text=best_text, language=_normalize_language(best_lang))


def create_indicconformer_stt(settings: Settings) -> IndicConformerSTT:
    auto_languages = [
        code.strip()
        for code in (settings.speech_stt_auto_languages or "").split(",")
        if code.strip()
    ]
    return IndicConformerSTT(
        settings.speech_stt_model,
        device=settings.speech_stt_device,
        auto_languages=auto_languages,
    )


__all__ = [
    "SUPPORTED_MIME_PREFIXES",
    "IndicConformerSTT",
    "create_indicconformer_stt",
    "mime_supported",
]
