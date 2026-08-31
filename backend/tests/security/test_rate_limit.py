"""Rate limiting tests (REQUIREMENTS D-049/D-050).

Adversarial: exceeding the chat/upload budget must produce the structured
429 envelope, not a stream, and must not consume the budget of another
session.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.rate_limit import RateLimiter, RateLimitExceededError, enforce_rate_limit
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.generation.fixtures import ScriptedProvider


def _app(chat_limit: int, upload_limit: int) -> FastAPI:
    settings = Settings(
        _env_file=None,
        llm_provider="stub",
        rate_limit_chat_per_minute=chat_limit,
        rate_limit_upload_per_minute=upload_limit,
    )
    app = create_app(settings=settings)
    app.state.llm_registry.register("stub", lambda _s: ScriptedProvider(["ok"] * 40))
    return app


def test_chat_rate_limit_returns_429_envelope() -> None:
    app = _app(chat_limit=2, upload_limit=5)
    client = TestClient(app)
    for _ in range(2):
        assert client.post("/api/v1/chat", json={"message": "hi"}).status_code == 200
    response = client.post("/api/v1/chat", json={"message": "hi"})
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == "RATE_LIMITED"
    assert "request_id" in error
    # The body is JSON, never an SSE stream.
    assert "text/event-stream" not in response.headers.get("content-type", "")


def test_chat_rate_limit_is_per_session() -> None:
    app = _app(chat_limit=1, upload_limit=5)
    client = TestClient(app)
    assert (
        client.post(
            "/api/v1/chat", json={"message": "hi"}, headers={"X-Session-Id": "session-one"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/chat", json={"message": "hi"}, headers={"X-Session-Id": "session-one"}
        ).status_code
        == 429
    )
    # A different session has its own budget.
    assert (
        client.post(
            "/api/v1/chat", json={"message": "hi"}, headers={"X-Session-Id": "session-two"}
        ).status_code
        == 200
    )


def test_upload_rate_limit_returns_429() -> None:
    from tests.documents.pdf_fixtures import make_pdf

    app = _app(chat_limit=20, upload_limit=1)
    client = TestClient(app)
    first = client.post(
        "/api/v1/documents/upload",
        files={"file": ("a.pdf", make_pdf(["tenant notice"]), "application/pdf")},
        headers={"X-Session-Id": "upload-session"},
    )
    assert first.status_code == 201
    second = client.post(
        "/api/v1/documents/upload",
        files={"file": ("b.pdf", make_pdf(["tenant notice"]), "application/pdf")},
        headers={"X-Session-Id": "upload-session"},
    )
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "RATE_LIMITED"


def test_limiter_window_slides() -> None:
    limiter = RateLimiter()
    assert limiter.allow("chat", "k", limit=1, window_seconds=0.05)
    assert not limiter.allow("chat", "k", limit=1, window_seconds=0.05)
    import time

    time.sleep(0.06)
    assert limiter.allow("chat", "k", limit=1, window_seconds=0.05)


def test_limiter_scopes_are_independent() -> None:
    limiter = RateLimiter()
    assert limiter.allow("chat", "k", limit=1, window_seconds=60.0)
    assert limiter.allow("upload", "k", limit=1, window_seconds=60.0)


def test_enforce_raises_typed_error() -> None:
    limiter = RateLimiter()
    enforce_rate_limit(limiter, scope="chat", key="k", limit=1, window_seconds=60.0)
    try:
        enforce_rate_limit(limiter, scope="chat", key="k", limit=1, window_seconds=60.0)
        raise AssertionError("expected RateLimitExceededError")
    except RateLimitExceededError as exc:
        assert exc.status_code == 429
        assert exc.code == "RATE_LIMITED"
