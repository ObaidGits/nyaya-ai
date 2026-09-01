"""Health and readiness endpoint tests (REQUIREMENTS.md D-028, D-029)."""

from typing import TYPE_CHECKING

import httpx
import pytest
from app.core.health import (
    CheckRegistry,
    CheckResult,
    CheckStatus,
    DependencyCheck,
    ModelProviderCheck,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from app.llm.base import ProviderHealth


class _FailingCheck(DependencyCheck):
    name = "failing_dependency"

    async def check(self) -> CheckResult:
        return CheckResult(name=self.name, status=CheckStatus.FAIL, detail="dependency is down")


class _RaisingCheck(DependencyCheck):
    name = "raising_dependency"

    async def check(self) -> CheckResult:
        raise RuntimeError("boom")


class _HealthyCheck(DependencyCheck):
    name = "healthy_dependency"

    async def check(self) -> CheckResult:
        return CheckResult(name=self.name, status=CheckStatus.OK)


def test_liveness_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_liveness_has_request_id_header(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.headers["x-request-id"]


def test_readiness_reports_registered_check(app: FastAPI) -> None:
    app.state.check_registry = CheckRegistry([_HealthyCheck()])
    response = TestClient(app).get("/api/v1/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["healthy_dependency"]["status"] == "ok"


def test_readiness_fails_when_dependency_fails(app: FastAPI) -> None:
    app.state.check_registry = CheckRegistry([_FailingCheck()])
    response = TestClient(app).get("/api/v1/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["failing_dependency"]["status"] == "fail"
    assert body["checks"]["failing_dependency"]["detail"] == "dependency is down"


def test_readiness_fails_when_check_raises(app: FastAPI) -> None:
    app.state.check_registry = CheckRegistry([_RaisingCheck()])
    response = TestClient(app).get("/api/v1/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["raising_dependency"]["status"] == "fail"


def test_readiness_reports_dependency_checks(app: FastAPI) -> None:
    """D-030/D-031/D-032: vector DB, model and storage checks are registered."""
    names = app.state.check_registry.names()
    assert "vector_db" in names
    assert "model" in names
    assert "storage" in names


# ---------------------------------------------------------------------------
# Model provider check: the configured model must actually be present
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _FakeClient:
    """Replaces httpx.AsyncClient inside ModelProviderCheck.check()."""

    response: _FakeResponse | None = None
    error: Exception | None = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def get(self, url: str) -> _FakeResponse:
        if _FakeClient.error is not None:
            raise _FakeClient.error
        assert _FakeClient.response is not None
        return _FakeClient.response


def _check(model: str | None) -> CheckResult:
    import asyncio

    check = ModelProviderCheck("http://ollama.local", "ollama", model)
    return asyncio.run(check.check())


def _mock_response(monkeypatch: pytest.MonkeyPatch, payload: object, status: int = 200) -> None:
    _FakeClient.response = _FakeResponse(status, payload)
    _FakeClient.error = None
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)


def test_model_check_ok_when_model_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_response(monkeypatch, {"models": [{"name": "llama3.1:8b"}]})
    result = _check("llama3.1:8b")
    assert result.status == CheckStatus.OK
    assert "llama3.1:8b" in (result.detail or "")


def test_model_check_ok_on_prefix_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asking for "llama3.1" is satisfied by the pulled "llama3.1:8b" tag."""
    _mock_response(monkeypatch, {"models": [{"name": "llama3.1:8b"}]})
    result = _check("llama3.1")
    assert result.status == CheckStatus.OK


def test_model_check_fails_when_model_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reachable server without the configured model is NOT healthy: the
    readiness probe must not report a brain the app cannot use."""
    _mock_response(monkeypatch, {"models": [{"name": "mistral:7b"}]})
    result = _check("llama3.1:8b")
    assert result.status == CheckStatus.FAIL
    assert "not present" in (result.detail or "")


def test_model_check_fails_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeClient.response = None
    _FakeClient.error = httpx.ConnectError("connection refused")
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    result = _check("llama3.1:8b")
    assert result.status == CheckStatus.FAIL


def test_model_check_transport_only_without_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_response(monkeypatch, {"models": []})
    result = _check(None)
    assert result.status == CheckStatus.OK


def test_model_check_fails_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BadJson(_FakeResponse):
        def json(self) -> object:
            raise ValueError("not json")

    _FakeClient.response = _BadJson(200, None)
    _FakeClient.error = None
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    result = _check("llama3.1:8b")
    assert result.status == CheckStatus.FAIL


# ---------------------------------------------------------------------------
# ActiveModelCheck: truthful check of the CURRENTLY configured provider
# ---------------------------------------------------------------------------


class _FakeActiveProvider:
    """Provider double whose probe() returns a canned health."""

    def __init__(self, health: "ProviderHealth") -> None:
        self._health = health

    async def probe(self) -> "ProviderHealth":
        return self._health


def _active_check(health: "ProviderHealth") -> CheckResult:
    import asyncio

    from app.core.health import ActiveModelCheck

    check = ActiveModelCheck(lambda: _FakeActiveProvider(health))
    return asyncio.run(check.check())


def test_active_model_check_ok_when_healthy() -> None:
    from app.llm.base import ProviderHealth, ProviderHealthState

    result = _active_check(
        ProviderHealth(state=ProviderHealthState.HEALTHY, provider="grok", model="grok-4.6")
    )
    assert result.status == CheckStatus.OK


def test_active_model_check_fails_with_state_detail() -> None:
    """Every non-healthy state is an honest FAIL with the reason."""
    from app.llm.base import ProviderHealth, ProviderHealthState

    result = _active_check(
        ProviderHealth(
            state=ProviderHealthState.INVALID_CONFIGURATION,
            provider="grok",
            model="grok-4.6",
            detail="The provider rejected the API key (HTTP 401).",
        )
    )
    assert result.status == CheckStatus.FAIL
    assert "invalid_configuration" in (result.detail or "")
    assert "401" in (result.detail or "")


def test_active_model_check_fails_when_resolver_raises() -> None:
    from app.core.health import ActiveModelCheck

    def _raise() -> object:
        raise RuntimeError("no provider registered")

    import asyncio

    result = asyncio.run(ActiveModelCheck(_raise).check())
    assert result.status == CheckStatus.FAIL


# ---------------------------------------------------------------------------
# Public LLM health endpoint (brain status contract)
# ---------------------------------------------------------------------------


def test_llm_health_reports_state(app: FastAPI) -> None:
    """/health/llm exposes the active provider's classified state."""
    from app.llm.base import ProviderHealth, ProviderHealthState

    class _Registry:
        def create(self, name: str, settings: object) -> _FakeActiveProvider:
            return _FakeActiveProvider(
                ProviderHealth(
                    state=ProviderHealthState.HEALTHY,
                    provider="grok",
                    model="grok-4.6",
                    detail="reachable and model available",
                )
            )

    app.state.llm_registry = _Registry()
    response = TestClient(app).get("/api/v1/health/llm")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "healthy"
    assert body["provider"] == "grok"
    assert body["model"] == "grok-4.6"
    assert "sk-" not in response.text  # never a secret


def test_llm_health_cache_and_invalidation(app: FastAPI) -> None:
    """The probe is cached briefly; a settings change invalidates it so the
    brain status follows console changes immediately."""
    from app.llm.base import ProviderHealth, ProviderHealthState

    calls: list[int] = []

    class _CountingProvider(_FakeActiveProvider):
        async def probe(self) -> ProviderHealth:
            calls.append(1)
            return await super().probe()

    health = ProviderHealth(state=ProviderHealthState.HEALTHY, provider="grok", model="m")

    class _Registry:
        def create(self, name: str, settings: object) -> _CountingProvider:
            return _CountingProvider(health)

    app.state.llm_registry = _Registry()
    client = TestClient(app)
    assert client.get("/api/v1/health/llm").json()["state"] == "healthy"
    assert client.get("/api/v1/health/llm").json()["state"] == "healthy"
    assert len(calls) == 1  # second call served from cache
    # A settings swap drops the cache (admin console does this on save).
    app.state.llm_health_cache = None
    assert client.get("/api/v1/health/llm").json()["state"] == "healthy"
    assert len(calls) == 2


def test_llm_health_not_configured_when_provider_unknown(app: FastAPI) -> None:
    from app.llm.registry import UnknownProviderError

    class _Registry:
        def create(self, name: str, settings: object) -> object:
            raise UnknownProviderError("unknown provider")

    app.state.llm_registry = _Registry()
    response = TestClient(app).get("/api/v1/health/llm")
    assert response.status_code == 200
    assert response.json()["state"] == "not_configured"


# ---------------------------------------------------------------------------
# Effective-config source: env vs persisted admin console (drift honesty)
# ---------------------------------------------------------------------------


def test_llm_health_config_source_environment(app: FastAPI) -> None:
    """No persisted console settings → the environment is the config source."""
    from app.llm.base import ProviderHealth, ProviderHealthState

    class _Registry:
        def create(self, name: str, settings: object) -> _FakeActiveProvider:
            return _FakeActiveProvider(
                ProviderHealth(state=ProviderHealthState.HEALTHY, provider="ollama", model="m")
            )

    app.state.llm_registry = _Registry()
    body = TestClient(app).get("/api/v1/health/llm").json()
    assert body["config_source"] == "environment"


def test_llm_health_config_source_admin_console(tmp_path: object) -> None:
    """Persisted console settings select the provider → the source must say
    so, even when the environment carries different (stale) LLM_* values."""
    import json
    from pathlib import Path

    from app.core.config import Settings
    from app.llm.base import ProviderHealth, ProviderHealthState
    from app.main import create_app

    path = Path(tmp_path) / "admin.json"
    path.write_text(json.dumps({"settings": {"llm_provider": "groq"}, "secrets": {}}))
    settings = Settings(_env_file=None, admin_settings_path=str(path))
    app = create_app(settings=settings)

    class _Registry:
        def create(self, name: str, settings: object) -> _FakeActiveProvider:
            return _FakeActiveProvider(
                ProviderHealth(
                    state=ProviderHealthState.HEALTHY, provider="groq", model="openai/gpt-oss-120b"
                )
            )

    app.state.llm_registry = _Registry()
    body = TestClient(app).get("/api/v1/health/llm").json()
    assert body["config_source"] == "admin_console"
    assert body["provider"] == "groq"
    assert body["model"] == "openai/gpt-oss-120b"
