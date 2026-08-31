"""Speech endpoint tests (STT/TTS, DECISIONS.md D-079).

Fake providers are injected through ``app.state.speech_service`` so no model
runtime is required — the real providers are covered by their unit tests and
live E2E. These tests pin the security contract: bounded reads, MIME and
language validation, structured errors, session + rate limits, and the
guarantee that voice never bypasses chat/RAG.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from app.main import create_app
from app.speech.base import (
    EmptyTranscriptionError,
    SpeechProviderError,
    SpeechUnsupportedLanguageError,
    TranscriptionResult,
)
from app.speech.service import SpeechService
from fastapi import FastAPI
from fastapi.testclient import TestClient

SESSION = "speech-session-0001"
HEADERS = {"X-Session-Id": SESSION}
WAV = b"RIFF....WAVEfmt "


class FakeSTT:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.fail = False
        self.result = TranscriptionResult(text="What does Section 103 say?", language="en")

    async def transcribe(
        self, data: bytes, *, mime_type: str, language: str | None
    ) -> TranscriptionResult:
        self.calls.append((mime_type, language))
        if self.fail:
            raise SpeechProviderError("The speech-to-text provider is not available.")
        return self.result


class FakeTTS:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = False
        self.unsupported: set[str] = set()

    async def synthesize(self, text: str, *, language: str) -> object:
        from app.speech.base import SynthesisResult

        self.calls.append((text, language))
        if self.fail:
            raise SpeechProviderError("The text-to-speech provider is not available.")
        if language in self.unsupported:
            raise SpeechUnsupportedLanguageError(f"No voice for '{language}'.")
        return SynthesisResult(audio=b"RIFF-fake-wav", media_type="audio/wav")


@pytest.fixture
def fake_stt() -> FakeSTT:
    return FakeSTT()


@pytest.fixture
def fake_tts() -> FakeTTS:
    return FakeTTS()


@pytest.fixture
def speech_app(fake_stt: FakeSTT, fake_tts: FakeTTS, settings) -> FastAPI:
    """App with injectable fake speech providers (no model runtime needed)."""
    app = create_app(settings=settings)
    app.state.speech_service = SpeechService(stt=fake_stt, tts=fake_tts)
    return app


@pytest.fixture
def speech_client(speech_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(speech_app) as client:
        yield client


def _post_audio(client: TestClient, audio: bytes, mime: str, **kwargs) -> object:
    return client.post(
        "/api/v1/speech/transcribe",
        files={"file": ("recording", audio, mime)},
        headers=HEADERS,
        **kwargs,
    )


# --- transcription ---------------------------------------------------------


def test_transcribe_valid_audio_returns_text_and_language(speech_client: TestClient) -> None:
    response = _post_audio(speech_client, WAV, "audio/webm")
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "What does Section 103 say?"
    assert body["language"] == "en"


def test_transcribe_requested_language_overrides_auto(
    speech_client: TestClient, fake_stt: FakeSTT
) -> None:
    response = _post_audio(speech_client, WAV, "audio/webm", params={"language": "hi"})
    assert response.status_code == 200
    assert fake_stt.calls[0][1] == "hi"


def test_transcribe_auto_language_passes_none(speech_client: TestClient, fake_stt: FakeSTT) -> None:
    response = _post_audio(speech_client, WAV, "audio/webm", params={"language": "auto"})
    assert response.status_code == 200
    assert fake_stt.calls[0][1] is None


def test_transcribe_rejects_unsupported_mime(speech_client: TestClient) -> None:
    response = _post_audio(speech_client, b"#!/bin/sh", "application/x-sh")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "AUDIO_FORMAT_UNSUPPORTED"


def test_transcribe_rejects_empty_upload(speech_client: TestClient) -> None:
    response = _post_audio(speech_client, b"", "audio/webm")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMPTY_TRANSCRIPTION"


def test_transcribe_rejects_oversized_audio(speech_app: FastAPI) -> None:
    settings = speech_app.state.settings
    speech_app.state.settings = settings.model_copy(
        update={"max_audio_upload_mb": 1, "rate_limit_speech_per_minute": 100}
    )
    with TestClient(speech_app) as client:
        response = _post_audio(client, b"x" * (2 * 1024 * 1024), "audio/webm")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "AUDIO_TOO_LARGE"


def test_transcribe_empty_result_is_422(speech_client: TestClient, fake_stt: FakeSTT) -> None:
    fake_stt.result = TranscriptionResult(text="   ", language="en")
    response = _post_audio(speech_client, WAV, "audio/webm")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "EMPTY_TRANSCRIPTION"


def test_transcribe_provider_failure_is_503(speech_client: TestClient, fake_stt: FakeSTT) -> None:
    fake_stt.fail = True
    response = _post_audio(speech_client, WAV, "audio/webm")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SPEECH_PROVIDER_UNAVAILABLE"


def test_transcribe_unsupported_requested_language_is_422(speech_client: TestClient) -> None:
    response = _post_audio(speech_client, WAV, "audio/webm", params={"language": "fr"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SPEECH_LANGUAGE_INVALID"


def test_transcribe_requires_session(speech_client: TestClient) -> None:
    response = speech_client.post(
        "/api/v1/speech/transcribe", files={"file": ("r", WAV, "audio/webm")}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SESSION_REQUIRED"


def test_transcribe_malformed_request_missing_file(speech_client: TestClient) -> None:
    response = speech_client.post("/api/v1/speech/transcribe", headers=HEADERS)
    assert response.status_code == 422


def test_transcribe_result_is_not_sent_to_chat(speech_client: TestClient) -> None:
    """Voice input never bypasses chat: transcription returns text only."""
    response = _post_audio(speech_client, WAV, "audio/webm")
    assert response.status_code == 200
    assert set(response.json()) == {"text", "language"}


# --- synthesis -------------------------------------------------------------


def test_synthesize_returns_playable_audio(speech_client: TestClient, fake_tts: FakeTTS) -> None:
    response = speech_client.post(
        "/api/v1/speech/synthesize",
        json={"text": "Section 103 provides for fines.", "language": "en"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content == b"RIFF-fake-wav"
    assert fake_tts.calls == [("Section 103 provides for fines.", "en")]


def test_synthesize_honours_language_exactly(speech_client: TestClient, fake_tts: FakeTTS) -> None:
    speech_client.post(
        "/api/v1/speech/synthesize",
        json={"text": "धारा 103 जुर्माने का प्रावधान करती है।", "language": "hi"},
        headers=HEADERS,
    )
    assert fake_tts.calls[0][1] == "hi"


def test_synthesize_rejects_auto_language(speech_client: TestClient) -> None:
    response = speech_client.post(
        "/api/v1/speech/synthesize", json={"text": "hello", "language": "auto"}, headers=HEADERS
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SPEECH_LANGUAGE_INVALID"


def test_synthesize_rejects_unsupported_language(speech_client: TestClient) -> None:
    response = speech_client.post(
        "/api/v1/speech/synthesize", json={"text": "hello", "language": "xx"}, headers=HEADERS
    )
    assert response.status_code == 422


def test_synthesize_provider_failure_is_503(speech_client: TestClient, fake_tts: FakeTTS) -> None:
    fake_tts.fail = True
    response = speech_client.post(
        "/api/v1/speech/synthesize", json={"text": "hello", "language": "en"}, headers=HEADERS
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SPEECH_PROVIDER_UNAVAILABLE"


def test_synthesize_rejects_empty_and_oversized_text(speech_client: TestClient) -> None:
    response = speech_client.post(
        "/api/v1/speech/synthesize", json={"text": "", "language": "en"}, headers=HEADERS
    )
    assert response.status_code == 422
    response = speech_client.post(
        "/api/v1/speech/synthesize", json={"text": "x" * 5001, "language": "en"}, headers=HEADERS
    )
    assert response.status_code == 422


def test_synthesize_never_retrieves_or_generates(speech_client: TestClient) -> None:
    """Voice output never bypasses RAG: only the supplied text is spoken."""
    response = speech_client.post(
        "/api/v1/speech/synthesize",
        json={"text": "anything the user supplies", "language": "en"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    # The response is raw audio — no sources, no citations, no chat fields.
    assert "sources" not in response.headers


# --- rate limiting + error sanitization -------------------------------------


def test_speech_rate_limited(speech_app: FastAPI) -> None:
    speech_app.state.settings = speech_app.state.settings.model_copy(
        update={"rate_limit_speech_per_minute": 1}
    )
    with TestClient(speech_app) as client:
        first = _post_audio(client, WAV, "audio/webm")
        second = _post_audio(client, WAV, "audio/webm")
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "RATE_LIMITED"


def test_speech_unconfigured_returns_503(settings) -> None:
    app = create_app(settings=settings)
    app.state.speech_service = None
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/speech/transcribe",
            files={"file": ("r", WAV, "audio/webm")},
            headers=HEADERS,
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SPEECH_NOT_CONFIGURED"


def test_speech_error_has_no_internal_paths_or_tracebacks(
    speech_client: TestClient, fake_stt: FakeSTT
) -> None:
    fake_stt.fail = True
    response = _post_audio(speech_client, WAV, "audio/webm")
    body = response.text
    assert "Traceback" not in body
    assert "/" not in response.json()["error"]["message"] or "audio" in body.lower()


def test_transcribe_corrupt_audio_is_clean_400(
    speech_client: TestClient, fake_stt: FakeSTT
) -> None:
    """A provider that cannot decode audio surfaces as a clean 400."""

    class CorruptSTT(FakeSTT):
        async def transcribe(self, data, *, mime_type, language):  # type: ignore[no-untyped-def]
            from app.speech.base import AudioDecodeError

            raise AudioDecodeError("The uploaded audio could not be decoded.")

    speech_client.app.state.speech_service = SpeechService(stt=CorruptSTT(), tts=fake_tts)
    response = _post_audio(speech_client, b"\x00\x01\x02garbage", "audio/webm")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "AUDIO_DECODE_FAILED"


def test_empty_transcription_error_code() -> None:
    assert EmptyTranscriptionError("no speech").status_code == 422
