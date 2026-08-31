"""Piper TTS provider tests (D-081 lightweight local default)."""

from __future__ import annotations

import asyncio
import sys
import types
import wave
from pathlib import Path
from typing import Any, ClassVar

import pytest
from app.speech.base import SpeechProviderError


class _Chunk:
    def __init__(self, pcm: bytes, sample_rate: int = 22050) -> None:
        self.audio_int16_bytes = pcm
        self.sample_rate = sample_rate


class _FakeVoice:
    """Mimics piper.PiperVoice: load() classmethod + synthesize() chunks."""

    calls: ClassVar[list[str]] = []

    @classmethod
    def load(cls, model: str, **_: Any) -> _FakeVoice:
        cls.calls.append(model)
        return cls()

    def synthesize(self: Any, text: str) -> list[_Chunk]:
        _FakeVoice.calls.append(text)
        return [_Chunk(b"\x00\x01" * 100)]


@pytest.fixture()
def fake_piper(monkeypatch: pytest.MonkeyPatch) -> type[_FakeVoice]:
    module = types.ModuleType("piper")
    module.PiperVoice = _FakeVoice  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "piper", module)
    _FakeVoice.calls = []
    return _FakeVoice


def _provider(tmp_path: Path, model: str = ""):
    from app.speech.piper_tts import PiperTTS

    voices = tmp_path / "voices"
    voices.mkdir(exist_ok=True)
    # Pretend the voices are baked in: file present → path-based load.
    for voice in ("en_US-lessac-medium", "hi_IN-pratham-medium", "en_US-amy-high"):
        (voices / f"{voice}.onnx").write_bytes(b"stub")
    return PiperTTS(model, voices)


def test_synthesize_builds_valid_wav(fake_piper: type[_FakeVoice], tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    result = asyncio.run(provider.synthesize("Hello there", language="en"))
    assert result.media_type == "audio/wav"
    with wave.open(__import__("io").BytesIO(result.audio)) as handle:
        assert handle.getframerate() == 22050
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getnframes() == 100
    # English default voice was resolved from the voices dir path.
    assert fake_piper.calls[0].endswith("en_US-lessac-medium.onnx")


def test_hindi_uses_pinned_voice(fake_piper: type[_FakeVoice], tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    asyncio.run(provider.synthesize("नमस्ते", language="hi"))
    assert fake_piper.calls[0].endswith("hi_IN-pratham-medium.onnx")


def test_unknown_language_falls_back_to_english(
    fake_piper: type[_FakeVoice], tmp_path: Path
) -> None:
    provider = _provider(tmp_path)
    asyncio.run(provider.synthesize("bonjour", language="fr"))
    assert fake_piper.calls[0].endswith("en_US-lessac-medium.onnx")


def test_configured_model_overrides_english_voice(
    fake_piper: type[_FakeVoice], tmp_path: Path
) -> None:
    provider = _provider(tmp_path, model="en_US-amy-high")
    asyncio.run(provider.synthesize("hi", language="en"))
    assert fake_piper.calls[0].endswith("en_US-amy-high.onnx")


def test_voice_load_failure_is_clean_503(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = types.ModuleType("piper")

    def _boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("onnx load crashed")

    module.PiperVoice = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "piper", module)
    provider = _provider(tmp_path)
    with pytest.raises(SpeechProviderError) as exc:
        asyncio.run(provider.synthesize("hello", language="en"))
    assert "voice could not be loaded" in str(exc.value)


def test_missing_piper_package_is_clean_503(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(sys.modules, "piper", None)  # import raises ImportError
    provider = _provider(tmp_path)
    with pytest.raises(SpeechProviderError) as exc:
        asyncio.run(provider.synthesize("hello", language="en"))
    assert "not installed" in str(exc.value)


def test_warm_up_preloads_and_survives_failures(
    fake_piper: type[_FakeVoice], tmp_path: Path
) -> None:
    provider = _provider(tmp_path)
    provider.warm_up()
    # Both configured voices (en, hi) loaded once; cache prevents reload.
    loaded = [c for c in fake_piper.calls if c.endswith(".onnx")]
    assert len(loaded) == 2
    provider.warm_up()
    loaded = [c for c in fake_piper.calls if c.endswith(".onnx")]
    assert len(loaded) == 2


def test_factory_defaults_voices_dir_under_storage(
    fake_piper: type[_FakeVoice], tmp_path: Path
) -> None:
    from app.core.config import Settings
    from app.speech.piper_tts import PiperTTS, create_piper_tts

    settings = Settings(storage_dir=str(tmp_path), speech_tts_model="en_US-lessac-medium")
    provider = create_piper_tts(settings)
    assert isinstance(provider, PiperTTS)
    assert provider._voices_dir == tmp_path / "piper-voices"

    explicit = Settings(
        storage_dir=str(tmp_path),
        speech_tts_voices_dir=str(tmp_path / "baked"),
        speech_tts_model="",
    )
    assert create_piper_tts(explicit)._voices_dir == tmp_path / "baked"


def test_tts_provider_dispatch_prefers_piper(fake_piper: type[_FakeVoice]) -> None:
    from app.core.config import Settings
    from app.speech.piper_tts import PiperTTS
    from app.speech.service import SpeechService

    # Default (no env override) is Piper — the lightweight local provider.
    assert isinstance(SpeechService(settings=Settings())._build_tts(), PiperTTS)
    explicit = Settings(speech_tts_provider="piper")
    assert isinstance(SpeechService(settings=explicit)._build_tts(), PiperTTS)
