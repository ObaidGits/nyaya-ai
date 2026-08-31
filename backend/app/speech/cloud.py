"""Cloud speech providers over an OpenAI-compatible HTTP API (D-079).

Opt-in only — never the default. Point ``SPEECH_STT_BASE_URL`` /
``SPEECH_TTS_BASE_URL`` at OpenAI, Groq, or any compatible gateway, set the
matching ``*_API_KEY`` and ``*_PROVIDER=openai``, and the same endpoints
transcribe/synthesize via that cloud instead of local models. Voice stays an
input/output layer: transcription returns text only, synthesis speaks only
the supplied text in the supplied language.
"""

from __future__ import annotations

import logging

import httpx

from app.core.config import Settings
from app.speech.base import (
    AudioDecodeError,
    SpeechProviderError,
    SpeechUnsupportedLanguageError,
    STTProvider,
    SynthesisResult,
    TranscriptionResult,
    TTSProvider,
)

logger = logging.getLogger(__name__)

_SUPPORTED = {"en", "hi", "bn", "mr", "gu", "ta", "te", "kn", "ml", "pa", "or", "as"}
_TIMEOUT_SECONDS = 120.0


class OpenAICompatibleSTT(STTProvider):  # type: ignore[misc]
    """POST /audio/transcriptions (multipart) to a configurable endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = _TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds

    async def transcribe(
        self, data: bytes, *, mime_type: str, language: str | None
    ) -> TranscriptionResult:
        if language and language not in _SUPPORTED:
            raise SpeechUnsupportedLanguageError(
                f"Speech recognition does not support language '{language}'."
            )
        if not self._api_key:
            raise SpeechProviderError(
                "Cloud speech-to-text is selected but no API key is configured."
            )
        files = {"file": ("recording.webm", data, mime_type or "audio/webm")}
        form: dict[str, str] = {"model": self._model, "response_format": "json"}
        if language:
            form["language"] = language
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    files=files,
                    data=form,
                )
        except httpx.HTTPError as exc:
            raise SpeechProviderError("The cloud speech-to-text request failed.") from exc
        if response.status_code != 200:
            raise SpeechProviderError("The cloud speech-to-text provider rejected the request.")
        payload = response.json()
        text = str(payload.get("text", "")).strip()
        return TranscriptionResult(text=text, language=language or "en")


class OpenAICompatibleTTS(TTSProvider):  # type: ignore[misc]
    """POST /audio/speech (JSON -> audio bytes) to a configurable endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        voice: str,
        timeout_seconds: float = _TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._timeout = timeout_seconds

    async def synthesize(self, text: str, *, language: str) -> SynthesisResult:
        if language not in _SUPPORTED:
            raise SpeechUnsupportedLanguageError(
                f"Speech synthesis does not support language '{language}'."
            )
        if not self._api_key:
            raise SpeechProviderError(
                "Cloud text-to-speech is selected but no API key is configured."
            )
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/audio/speech",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "voice": self._voice,
                        "input": text,
                        "response_format": "wav",
                    },
                )
        except httpx.HTTPError as exc:
            raise SpeechProviderError("The cloud text-to-speech request failed.") from exc
        if response.status_code != 200:
            raise SpeechProviderError("The cloud text-to-speech provider rejected the request.")
        audio = response.content
        if not audio:
            raise AudioDecodeError("The cloud speech provider returned no audio.")
        return SynthesisResult(audio=audio, media_type="audio/wav")


def _key(value: object) -> str:
    return value.get_secret_value() if hasattr(value, "get_secret_value") else ""


def create_openai_stt(settings: Settings) -> OpenAICompatibleSTT:
    return OpenAICompatibleSTT(
        base_url=settings.speech_stt_base_url,
        api_key=_key(settings.speech_stt_api_key),
        model=settings.speech_stt_model,
    )


def create_openai_tts(settings: Settings) -> OpenAICompatibleTTS:
    return OpenAICompatibleTTS(
        base_url=settings.speech_tts_base_url,
        api_key=_key(settings.speech_tts_api_key),
        model=settings.speech_tts_model,
        voice=settings.speech_tts_voice,
    )


__all__ = [
    "OpenAICompatibleSTT",
    "OpenAICompatibleTTS",
    "create_openai_stt",
    "create_openai_tts",
]
