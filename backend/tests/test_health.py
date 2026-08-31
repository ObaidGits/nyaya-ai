"""Health and readiness endpoint tests (REQUIREMENTS.md D-028, D-029)."""

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
