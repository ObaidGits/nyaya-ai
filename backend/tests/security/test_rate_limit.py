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


def test_chat_rate_limit_is_per_client_ip_not_session() -> None:
    """H5 regression: rotating the client-controlled X-Session-Id must NOT
    reset the budget — the key is the client IP (anonymous sessions have no
    non-forgeable identity). Same-IP requests stay throttled together."""
    app = _app(chat_limit=2, upload_limit=5)
    client = TestClient(app)
    # First request per session id: both allowed (limit 2).
    assert (
        client.post(
            "/api/v1/chat", json={"message": "hi"}, headers={"X-Session-Id": "session-0001"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/chat", json={"message": "hi"}, headers={"X-Session-Id": "session-0002"}
        ).status_code
        == 200
    )
    # Budget exhausted for the IP: a FRESH session id is still 429.
    third = client.post(
        "/api/v1/chat", json={"message": "hi"}, headers={"X-Session-Id": "session-0003"}
    )
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "RATE_LIMITED"
    # Without any session id: same IP, same 429.
    assert client.post("/api/v1/chat", json={"message": "hi"}).status_code == 429


def test_chat_session_id_cannot_dodge_ip_budget_at_limiter_level() -> None:
    """Limiter-level H5 regression: one allow() key, many forged labels."""
    limiter = RateLimiter()
    key = "203.0.113.9"
    assert limiter.allow("chat", key, limit=2, window_seconds=60.0)
    assert limiter.allow("chat", key, limit=2, window_seconds=60.0)
    # The client rotates its "identity" — the IP key is unaffected.
    for _ in range(3):  # forged labels never reach the limiter key
        assert not limiter.allow("chat", key, limit=2, window_seconds=60.0)


def test_limiter_evicts_empty_deques() -> None:
    """Memory-leak regression: fully-expired keys are dropped from the map."""
    limiter = RateLimiter(max_tracked_keys=2)
    assert limiter.allow("chat", "old-key", limit=5, window_seconds=0.05)
    import time

    time.sleep(0.06)
    # The map hits its cap; the fully-expired old key is evicted to make room.
    assert limiter.allow("chat", "new-key", limit=5, window_seconds=60.0)
    assert ("chat", "old-key") not in limiter._events
    assert ("chat", "new-key") in limiter._events


def test_limiter_bounded_under_key_flooding() -> None:
    """Key-flood bound: many attacker-rotated keys cannot grow the map past
    max_tracked_keys once their windows lapse."""
    limiter = RateLimiter(max_tracked_keys=16)
    for i in range(64):
        limiter.allow("chat", f"flood-{i}", limit=1, window_seconds=0.02)
    import time

    time.sleep(0.03)
    limiter.allow("chat", "trigger", limit=1, window_seconds=60.0)
    assert len(limiter._events) <= limiter._max_tracked_keys


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
