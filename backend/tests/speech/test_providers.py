"""Speech provider unit tests (pure logic; no model runtime required).

The heavyweight model paths are exercised live (Docker E2E); these tests pin
the provider contracts: MIME acceptance, language normalization, lazy-load
failure mode, and the TTS language-exactness contract.
"""

from __future__ import annotations

import asyncio

import pytest
from app.speech.base import (
    SpeechProviderError,
    SpeechUnsupportedLanguageError,
    TranscriptionResult,
)
from app.speech.indicconformer import IndicConformerSTT, mime_supported
from app.speech.parler_tts import IndicParlerTTS
from app.speech.service import SpeechService


@pytest.mark.parametrize(
    ("mime", "accepted"),
    [
        ("audio/webm", True),
        ("audio/webm;codecs=opus", True),
        ("audio/wav", True),
        ("audio/mpeg", True),
        ("audio/mp4", True),
        ("audio/ogg", True),
        ("application/pdf", False),
        ("application/x-sh", False),
        (None, False),
        ("", False),
    ],
)
def test_mime_supported(mime: str | None, accepted: bool) -> None:
    assert mime_supported(mime) is accepted


def test_indicconformer_load_failure_is_clean_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing torch/weights must surface as 503, never a traceback."""

    def _fail(name: str) -> object:
        raise ImportError(name)

    monkeypatch.setattr("builtins.__import__", _fail)
    provider = IndicConformerSTT("ai4bharat/indic-conformer-600m-multilingual")
    with pytest.raises(SpeechProviderError) as excinfo:
        provider._load()
    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "SPEECH_PROVIDER_UNAVAILABLE"
    assert "traceback" not in str(excinfo.value.message).lower()


def test_indicconformer_auto_languages_from_settings() -> None:
    provider = IndicConformerSTT("m", auto_languages=["en", "hi"])
    assert provider.auto_languages == ["en", "hi"]


def test_parler_tts_rejects_unknown_language() -> None:
    provider = IndicParlerTTS("m")
    with pytest.raises(SpeechUnsupportedLanguageError):
        import asyncio

        asyncio.run(provider.synthesize("hello", language="xx"))


def test_service_transcribe_validates_language_passthrough() -> None:
    class RecordingSTT:
        def __init__(self) -> None:
            self.seen: str | None = None

        async def transcribe(self, data: bytes, *, mime_type: str, language: str | None):
            self.seen = language
            return TranscriptionResult(text="नमस्ते", language="hi")

    stt = RecordingSTT()

    class _NullTTS:
        async def synthesize(self, text: str, *, language: str):
            raise AssertionError("TTS must not be called for transcription")

    import asyncio

    service = SpeechService(stt=stt, tts=_NullTTS())
    result = asyncio.run(service.transcribe(b"audio", mime_type="audio/webm", language="hi"))
    assert result.text == "नमस्ते"
    assert stt.seen == "hi"


def test_service_synthesize_returns_audio_bytes_only() -> None:
    from app.speech.base import SynthesisResult

    class _TTS:
        async def synthesize(self, text: str, *, language: str):
            assert language == "bn"
            return SynthesisResult(audio=b"wav-bytes", media_type="audio/wav")

    class _NullSTT:
        async def transcribe(self, data: bytes, *, mime_type: str, language: str | None):
            raise AssertionError("STT must not be called for synthesis")

    import asyncio

    service = SpeechService(stt=_NullSTT(), tts=_TTS())
    audio = asyncio.run(service.synthesize("উত্তর", language="bn"))
    assert audio == b"wav-bytes"


def test_speech_service_warm_up_loads_providers_without_raising() -> None:
    """SPEECH_PRELOAD path: warm_up calls provider warm_up, swallows errors."""
    from app.speech.service import SpeechService

    calls: list[str] = []

    class WarmSTT:
        def warm_up(self) -> None:
            calls.append("stt")

        async def transcribe(self, data: bytes, *, mime_type: str, language: str | None):
            raise AssertionError("not used")

    class BrokenTTS:
        def warm_up(self) -> None:
            raise RuntimeError("boom")

        async def synthesize(self, text: str, *, language: str):
            raise AssertionError("not used")

    service = SpeechService(stt=WarmSTT(), tts=BrokenTTS())  # type: ignore[arg-type]
    asyncio.run(service.warm_up())
    assert calls == ["stt"]  # stt warmed, tts failure swallowed


def test_stt_provider_dispatch_by_settings() -> None:
    """SPEECH_STT_PROVIDER selects the right provider class (D-079)."""
    from app.core.config import Settings
    from app.speech.faster_whisper_stt import FasterWhisperSTT
    from app.speech.service import SpeechService
    from app.speech.whisper_stt import WhisperSTT

    def build(provider: str) -> object:
        settings = Settings(speech_stt_provider=provider, speech_stt_model="small")
        return SpeechService(settings=settings)._build_stt()

    assert isinstance(build("faster-whisper"), FasterWhisperSTT)
    assert isinstance(build("whisper"), WhisperSTT)

    class _OpenAIStub:
        pass

    import app.speech.cloud as cloud

    orig = cloud.create_openai_stt
    cloud.create_openai_stt = lambda s: _OpenAIStub()
    try:
        assert isinstance(build("openai"), _OpenAIStub)
    finally:
        cloud.create_openai_stt = orig

    from app.speech.base import SpeechProviderError

    with pytest.raises(SpeechProviderError):
        build("nope")


def test_tts_provider_dispatch_by_settings() -> None:
    from app.core.config import Settings
    from app.speech.parler_tts import IndicParlerTTS
    from app.speech.service import SpeechService

    settings = Settings(speech_tts_provider="parler-tts", speech_tts_model="x")
    assert isinstance(SpeechService(settings=settings)._build_tts(), IndicParlerTTS)

    import app.speech.cloud as cloud

    class _Stub:
        pass

    orig = cloud.create_openai_tts
    cloud.create_openai_tts = lambda s: _Stub()
    try:
        settings = Settings(speech_tts_provider="openai")
        assert isinstance(SpeechService(settings=settings)._build_tts(), _Stub)
    finally:
        cloud.create_openai_tts = orig
