"""Health and readiness endpoint tests (REQUIREMENTS.md D-028, D-029)."""

from app.core.health import (
    CheckResult,
    CheckStatus,
    DependencyCheck,
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
    app.state.check_registry.add(_HealthyCheck())
    response = TestClient(app).get("/api/v1/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["configuration"]["status"] == "ok"
    assert body["checks"]["healthy_dependency"]["status"] == "ok"


def test_readiness_fails_when_dependency_fails(app: FastAPI) -> None:
    app.state.check_registry.add(_FailingCheck())
    response = TestClient(app).get("/api/v1/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["failing_dependency"]["status"] == "fail"
    assert body["checks"]["failing_dependency"]["detail"] == "dependency is down"
    # The healthy configuration check is still reported individually.
    assert body["checks"]["configuration"]["status"] == "ok"


def test_readiness_fails_when_check_raises(app: FastAPI) -> None:
    app.state.check_registry.add(_RaisingCheck())
    response = TestClient(app).get("/api/v1/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["checks"]["raising_dependency"]["status"] == "fail"
