"""Security hardening regressions: H5/H7, M12/M13, search/feedback budgets,
gemini model quoting, docs/llm-detail disclosure (2026-09 security pass)."""

from __future__ import annotations

import pytest
from app.core.config import Settings
from app.core.rate_limit import RateLimiter
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.generation.fixtures import ScriptedProvider

# --- M12: chat history bounds -----------------------------------------------------


def _history_app() -> FastAPI:
    settings = Settings(_env_file=None, llm_provider="stub")
    app = create_app(settings=settings)
    app.state.llm_registry.register("stub", lambda _s: ScriptedProvider(["ok"] * 10))
    return app


def test_chat_history_huge_content_rejected() -> None:
    """A 5 MB history turn must fail validation (422), not reach the LLM."""
    app = _history_app()
    client = TestClient(app)
    body = {
        "message": "hi",
        "history": [{"role": "user", "content": "x" * (5 * 1024 * 1024)}],
    }
    response = client.post("/api/v1/chat", json=body)
    assert response.status_code == 422


def test_chat_history_too_many_turns_rejected() -> None:
    """More than the hard 40-turn ceiling must 422."""
    app = _history_app()
    client = TestClient(app)
    body = {
        "message": "hi",
        "history": [{"role": "user", "content": "ok"}] * 41,
    }
    assert client.post("/api/v1/chat", json=body).status_code == 422


def test_chat_history_within_bounds_accepted() -> None:
    app = _history_app()
    client = TestClient(app)
    body = {
        "message": "hi",
        "history": [{"role": "user", "content": "ok"}] * 40,
    }
    assert client.post("/api/v1/chat", json=body).status_code == 200


def test_chat_history_single_turn_over_char_limit_rejected() -> None:
    app = _history_app()
    client = TestClient(app)
    body = {
        "message": "hi",
        "history": [{"role": "assistant", "content": "y" * 8001}],
    }
    assert client.post("/api/v1/chat", json=body).status_code == 422


# --- M13: admin login brute force -----------------------------------------------


def _admin_app() -> FastAPI:
    settings = Settings(
        _env_file=None,
        llm_provider="stub",
        admin_username="admin",
        admin_password="secret-password",  # type: ignore[arg-type]
    )
    app = create_app(settings=settings)
    app.state.llm_registry.register("stub", lambda _s: ScriptedProvider(["ok"] * 10))
    return app


def test_admin_login_rate_limited_after_5_attempts() -> None:
    app = _admin_app()
    client = TestClient(app)
    bad = {"username": "admin", "password": "wrong"}
    for _ in range(5):
        response = client.post("/api/v1/admin/login", json=bad)
        assert response.status_code == 401
    sixth = client.post("/api/v1/admin/login", json=bad)
    assert sixth.status_code == 429
    assert sixth.json()["error"]["code"] == "RATE_LIMITED"
    # Even CORRECT credentials are throttled while the window is hot.
    good = {"username": "admin", "password": "secret-password"}
    assert client.post("/api/v1/admin/login", json=good).status_code == 429


def test_admin_login_success_within_budget() -> None:
    app = _admin_app()
    client = TestClient(app)
    response = client.post(
        "/api/v1/admin/login", json={"username": "admin", "password": "secret-password"}
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


# --- H7: metrics auth + route-template labels -----------------------------------


def test_metrics_route_labels_use_template_not_raw_path() -> None:
    """Junk paths produce the single 'unmatched' label, never their own."""
    settings = Settings(_env_file=None, llm_provider="stub")
    app = create_app(settings=settings)
    client = TestClient(app)
    for junk in ("/api/v1/aardvark", "/api/v1/zebra/../secrets", "/api/v1/aaaaaaa"):
        client.get(junk)
    body = client.get("/api/v1/metrics").text
    assert 'route="unmatched"' in body
    for junk in ("aardvark", "zebra", "aaaaaaa"):
        assert f'/{junk}"' not in body, junk
    # Bounded by method x status combinations, NOT by path: even with the
    # shared process-wide registry carrying series from the other tests in
    # this module, the label set stays tiny and independent of path count.
    lines = [
        ln
        for ln in body.splitlines()
        if 'route="unmatched"' in ln and ln.startswith("nyaya_requests_total")
    ]
    assert len(lines) <= 10
    # All unmatched series share the fixed label: no per-path junk series.
    assert all('route="unmatched"' in ln for ln in lines)


def test_metrics_unauthenticated_in_dev_warns_but_serves() -> None:
    """No admin and no token: dev instance stays open (single warning)."""
    settings = Settings(_env_file=None, llm_provider="stub")
    app = create_app(settings=settings)
    client = TestClient(app)
    response = client.get("/api/v1/metrics")
    assert response.status_code == 200


def test_metrics_token_required_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METRICS_TOKEN", "tok-123")
    settings = Settings(_env_file=None, llm_provider="stub")
    app = create_app(settings=settings)
    client = TestClient(app)
    response = client.get("/api/v1/metrics")
    assert response.status_code == 401
    ok = client.get("/api/v1/metrics", headers={"Authorization": "Bearer tok-123"})
    assert ok.status_code == 200


def test_metrics_admin_cookie_grants_access() -> None:
    app = _admin_app()
    client = TestClient(app)
    client.post(
        "/api/v1/admin/login", json={"username": "admin", "password": "secret-password"}
    )
    response = client.get("/api/v1/metrics")
    assert response.status_code == 200


# --- Search + feedback budgets ----------------------------------------------------


def test_search_rate_limited_per_ip() -> None:
    """Budget fires before retrieval, so a 429 escapes even without a corpus
    configured (RETRIEVAL_NOT_CONFIGURED would otherwise mask it)."""
    settings = Settings(
        _env_file=None, llm_provider="stub", rate_limit_chat_per_minute=3
    )
    app = create_app(settings=settings)
    app.state.llm_registry.register("stub", lambda _s: ScriptedProvider(["ok"] * 10))
    client = TestClient(app)
    headers = {"X-Session-Id": "search-session-1"}
    responses = [
        client.post("/api/v1/search", json={"query": "murder"}, headers=headers)
        for _ in range(4)
    ]
    # The first three hit the 503 (no corpus in this hermetic app) but pass
    # the budget gate; the fourth is denied by the limiter BEFORE retrieval.
    assert [r.status_code for r in responses[:3]] == [503] * 3
    fourth = responses[3]
    assert fourth.status_code == 429
    assert fourth.json()["error"]["code"] == "RATE_LIMITED"


def test_feedback_rate_limited_per_ip() -> None:
    settings = Settings(_env_file=None, llm_provider="stub")
    app = create_app(settings=settings)
    app.state.llm_registry.register("stub", lambda _s: ScriptedProvider(["ok"] * 10))
    client = TestClient(app)
    headers = {"X-Session-Id": "feedback-session-1"}
    payload = {"vote": "up", "comment": None}
    statuses = [
        client.post("/api/v1/feedback", json=payload, headers=headers).status_code
        for _ in range(31)
    ]
    assert statuses[:30] == [201] * 30
    assert statuses[30] == 429


# --- LOW: gemini model name quoting ----------------------------------------------


def test_gemini_model_segment_is_percent_encoded() -> None:
    from app.llm.gemini import GeminiProvider

    provider = GeminiProvider(
        api_key="k", model="models/../gemini-2.0-flash?x=1", base_url="https://example.test/v1beta"
    )
    assert provider._model_segment == "models%2F..%2Fgemini-2.0-flash%3Fx%3D1"


# --- Docs + provider-detail gating ------------------------------------------------


def test_openapi_disabled_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    settings = Settings(_env_file=None, llm_provider="stub", environment="production")
    app = create_app(settings=settings)
    client = TestClient(app)
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_openapi_enabled_in_local_dev() -> None:
    settings = Settings(_env_file=None, llm_provider="stub")
    app = create_app(settings=settings)
    client = TestClient(app)
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_llm_health_hides_provider_details_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHOW_PROVIDER_DETAILS", raising=False)
    settings = Settings(_env_file=None, llm_provider="stub", environment="production")
    app = create_app(settings=settings)
    app.state.llm_registry.register("stub", lambda _s: ScriptedProvider(["ok"] * 10))
    client = TestClient(app)
    response = client.get("/api/v1/health/llm")
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] is None
    assert data["model"] is None


def test_llm_health_shows_provider_details_in_local_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SHOW_PROVIDER_DETAILS", raising=False)
    settings = Settings(_env_file=None, llm_provider="stub")
    app = create_app(settings=settings)
    app.state.llm_registry.register("stub", lambda _s: ScriptedProvider(["ok"] * 10))
    client = TestClient(app)
    response = client.get("/api/v1/health/llm")
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] is not None


# --- Eviction behavior (limiter) ---------------------------------------------------


def test_limiter_still_limits_within_window_after_eviction_work() -> None:
    limiter = RateLimiter()
    assert limiter.allow("chat", "k", limit=1, window_seconds=60.0)
    assert not limiter.allow("chat", "k", limit=1, window_seconds=60.0)
    assert not limiter.allow("chat", "k", limit=1, window_seconds=60.0)
