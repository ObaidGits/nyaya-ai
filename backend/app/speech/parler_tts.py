"""AI4Bharat Indic Parler-TTS provider (default TTS backend, D-079).

Real local speech synthesis over ``ai4bharat/indic-parler-tts`` via the
``parler_tts`` package. The model is heavy (~3 GB) so it is loaded lazily on
first use; the device is independently configurable (``SPEECH_TTS_DEVICE``)
because STT and TTS must not both sit permanently on a 6 GB GPU. Each
supported language maps to a fixed code-controlled voice description — the
caller's requested language is honoured exactly, never swapped.

Dependencies are imported inside methods: the API boots and fails closed
(503 SPEECH_PROVIDER_UNAVAILABLE) without them.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.config import Settings
from app.speech.base import (
    SpeechProviderError,
    SpeechUnsupportedLanguageError,
    SynthesisResult,
)

logger = logging.getLogger(__name__)

#: Per-language voice descriptions (code-controlled, never user-supplied).
_VOICE_DESCRIPTIONS: dict[str, str] = {
    "en": "Laura's voice is clear, calm and professional, speaking at a steady pace.",
    "hi": "Sunita speaks slowly in Hindi with a calm, clear and professional tone.",
    "bn": "A clear female voice reads the Bengali text calmly and at a steady pace.",
    "mr": "A clear female voice reads the Marathi text calmly and at a steady pace.",
    "gu": "A clear female voice reads the Gujarati text calmly and at a steady pace.",
    "ta": "A clear female voice reads the Tamil text calmly and at a steady pace.",
    "te": "A clear female voice reads the Telugu text calmly and at a steady pace.",
    "kn": "A clear female voice reads the Kannada text calmly and at a steady pace.",
    "ml": "A clear female voice reads the Malayalam text calmly and at a steady pace.",
    "pa": "A clear female voice reads the Punjabi text calmly and at a steady pace.",
    "or": "A clear female voice reads the Odia text calmly and at a steady pace.",
    "as": "A clear female voice reads the Assamese text calmly and at a steady pace.",
}


class IndicParlerTTS:
    """Lazy-loading local TTS provider backed by Indic Parler-TTS."""

    def __init__(self, model_name: str, *, device: str = "auto") -> None:
        self.model_name = model_name
        self.device_setting = device
        self._model: Any = None
        self._tokenizer: Any = None
        self._device: str = "cpu"

    def _resolve_device(self, torch_module: Any) -> str:
        if self.device_setting != "auto":
            return self.device_setting
        return "cuda" if torch_module.cuda.is_available() else "cpu"

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from parler_tts import (  # type: ignore[import-untyped]
                ParlerTTSForConditionalGeneration,
            )
            from transformers import AutoTokenizer  # type: ignore[import-untyped]

            self._device = self._resolve_device(torch)
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            self._model = ParlerTTSForConditionalGeneration.from_pretrained(self.model_name).to(
                self._device
            )
            self._model.eval()
        except Exception as exc:
            logger.warning(
                "indic parler-tts unavailable",
                extra={"model": self.model_name, "error_type": type(exc).__name__},
            )
            self._model = None
            raise SpeechProviderError(
                "The text-to-speech provider is not available on this instance."
            ) from exc

    def warm_up(self) -> None:
        """Eagerly load weights (startup pre-load, D-079 latency)."""
        self._load()

    def _generate(self, text: str, description: str) -> bytes:
        """Run model.generate and return WAV bytes."""
        import io

        import soundfile as sf  # type: ignore[import-untyped]
        import torch

        description_input = self._tokenizer(description, return_tensors="pt").to(self._device)
        prompt_input = self._tokenizer(text, return_tensors="pt").to(self._device)
        with torch.no_grad():
            audio_array = self._model.generate(
                input_ids=description_input.input_ids,
                attention_mask=description_input.attention_mask,
                prompt_input_ids=prompt_input.input_ids,
                prompt_attention_mask=prompt_input.attention_mask,
            )
        waveform = audio_array.cpu().numpy().squeeze()
        buffer = io.BytesIO()
        sf.write(buffer, waveform, 44100, format="WAV")
        return buffer.getvalue()

    async def synthesize(self, text: str, *, language: str) -> SynthesisResult:
        description = _VOICE_DESCRIPTIONS.get(language)
        if description is None:
            raise SpeechUnsupportedLanguageError(
                f"Speech synthesis does not support language '{language}'."
            )
        self._load()
        try:
            audio = await asyncio.to_thread(self._generate, text, description)
        except SpeechProviderError:
            raise
        except Exception:
            logger.warning("parler-tts synthesis failed", exc_info=True)
            raise SpeechProviderError("Speech synthesis failed. Please try again.") from None
        return SynthesisResult(audio=audio, media_type="audio/wav")


def create_parler_tts(settings: Settings) -> IndicParlerTTS:
    return IndicParlerTTS(settings.speech_tts_model, device=settings.speech_tts_device)


__all__ = ["IndicParlerTTS", "create_parler_tts"]
