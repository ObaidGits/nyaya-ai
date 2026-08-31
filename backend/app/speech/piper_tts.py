"""Piper TTS provider (lightweight local default, DECISIONS D-079/D-081).

``piper-tts`` runs 30-70 MB ONNX voices on CPU with ~100 ms synthesis —
roughly 100x faster than Parler-TTS and ~200 MB RAM instead of ~3 GB, which
removes the nginx 502 timeouts the heavy model caused. Voices are pinned
per language (code-controlled); the English voice is configurable via
``SPEECH_TTS_MODEL`` so operators can swap quality (e.g.
``en_US-amy-high``). Voices are resolved from the voices directory and
auto-downloaded there on first use when the directory is writable; Docker
images bake them in at build time.
"""

from __future__ import annotations

import asyncio
import io
import logging
import wave
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.speech.base import SpeechProviderError, SynthesisResult

logger = logging.getLogger(__name__)

#: Supported language → pinned Piper voice (quality/size balance). The
#: English entry is overridden by ``SPEECH_TTS_MODEL`` when set.
_LANGUAGE_VOICES: dict[str, str] = {
    "en": "en_US-lessac-medium",
    "hi": "hi_IN-pratham-medium",
}

_FALLBACK_LANGUAGE = "en"


class PiperTTS:
    """Lazy-loading local TTS provider backed by Piper."""

    def __init__(self, default_voice: str, voices_dir: Path) -> None:
        self._voices = dict(_LANGUAGE_VOICES)
        if default_voice:
            self._voices["en"] = default_voice
        self._voices_dir = voices_dir
        self._loaded: dict[str, Any] = {}

    def _voice_for(self, language: str) -> str:
        return self._voices.get(language, self._voices[_FALLBACK_LANGUAGE])

    def _load(self, language: str) -> Any:
        """Load (and cache) the Piper voice for a language."""
        voice = self._voice_for(language)
        if voice not in self._loaded:
            try:
                from piper import PiperVoice  # type: ignore[import-not-found]
            except ImportError as exc:
                raise SpeechProviderError(
                    "piper-tts is not installed; speech synthesis is unavailable.",
                    code="SPEECH_PROVIDER_UNAVAILABLE",
                ) from exc
            try:
                self._voices_dir.mkdir(parents=True, exist_ok=True)
                model_path = self._voices_dir / f"{voice}.onnx"
                if model_path.exists():
                    instance = PiperVoice.load(str(model_path))
                else:
                    # First use: Piper downloads the voice into the directory.
                    instance = PiperVoice.load(voice, download_dir=str(self._voices_dir))
            except Exception as exc:
                logger.warning(
                    "piper voice load failed",
                    extra={"voice": voice, "error_type": type(exc).__name__},
                )
                raise SpeechProviderError(
                    "The speech synthesis voice could not be loaded."
                ) from exc
            self._loaded[voice] = instance
        return self._loaded[voice]

    def _synthesize_wav(self, text: str, language: str) -> bytes:
        voice = self._load(language)
        chunks = list(voice.synthesize(text))
        if not chunks:
            raise SpeechProviderError("Speech synthesis produced no audio.")
        sample_rate = chunks[0].sample_rate
        pcm = b"".join(chunk.audio_int16_bytes for chunk in chunks)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(pcm)
        return buffer.getvalue()

    async def synthesize(self, text: str, *, language: str) -> SynthesisResult:
        try:
            audio = await asyncio.to_thread(self._synthesize_wav, text, language)
        except SpeechProviderError:
            raise
        except Exception:
            logger.warning("piper synthesis failed", exc_info=True)
            raise SpeechProviderError("Speech synthesis failed. Please try again.") from None
        return SynthesisResult(audio=audio, media_type="audio/wav")

    def warm_up(self) -> None:
        """Preload every configured voice so first requests stay fast."""
        for language in self._voices:
            try:
                self._load(language)
            except SpeechProviderError:
                logger.warning("piper warm-up failed", extra={"language": language})


def create_piper_tts(settings: Settings) -> PiperTTS:
    voices_dir = (
        Path(settings.speech_tts_voices_dir)
        if settings.speech_tts_voices_dir
        else Path(settings.storage_dir) / "piper-voices"
    )
    return PiperTTS(settings.speech_tts_model, voices_dir)


__all__ = ["PiperTTS", "create_piper_tts"]
