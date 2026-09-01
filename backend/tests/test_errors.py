"""Error handling tests (REQUIREMENTS.md Part D error contract).

Phase 1 exposes only health endpoints, so test-only routes are mounted on a
freshly built application to exercise the error handlers. The routes exist
only inside this test module and are not part of the product API.
"""

import pytest
from app.core.errors import (
    AppError,
    ErrorDetail,
    LLMRateLimitError,
    LLMTimeoutError,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _mount_test_routes(app: FastAPI) -> None:
    @app.get("/api/v1/_test/validation")
    async def _validation(quantity: int) -> dict[str, int]:
        return {"quantity": quantity}

    @app.get("/api/v1/_test/app-error")
    async def _app_error() -> None:
        raise AppError(
            "Something application-specific failed.",
            status_code=400,
            code="TEST_APP_ERROR",
            details=[ErrorDetail(location="query", message="explanatory detail")],
        )

    @app.get("/api/v1/_test/rate-limit")
    async def _rate_limit() -> None:
        raise LLMRateLimitError()

    @app.get("/api/v1/_test/timeout")
    async def _timeout() -> None:
        raise LLMTimeoutError()

    @app.get("/api/v1/_test/unexpected")
    async def _unexpected() -> None:
        raise RuntimeError("secret internal detail")


@pytest.fixture
def routed_app(app: FastAPI) -> FastAPI:
    _mount_test_routes(app)
    return app


def test_validation_error_has_consistent_structure(routed_app: FastAPI) -> None:
    client = TestClient(routed_app)
    response = client.get("/api/v1/_test/validation", params={"quantity": "not-a-number"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["message"] == "Request validation failed."
    assert body["error"]["request_id"]
    assert body["error"]["details"][0]["location"] == "query.quantity"
    # The invalid user input must not be echoed back.
    assert "not-a-number" not in response.text


def test_app_error_has_consistent_structure(routed_app: FastAPI) -> None:
    client = TestClient(routed_app)
    response = client.get("/api/v1/_test/app-error")
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "TEST_APP_ERROR"
    assert body["error"]["message"] == "Something application-specific failed."
    assert body["error"]["request_id"]
    assert body["error"]["details"][0]["location"] == "query"


def test_unexpected_error_does_not_leak_internals(routed_app: FastAPI) -> None:
    client = TestClient(routed_app, raise_server_exceptions=False)
    response = client.get("/api/v1/_test/unexpected")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["error"]["message"] == "An unexpected error occurred."
    assert body["error"]["request_id"]
    assert "secret internal detail" not in response.text
    assert "RuntimeError" not in response.text
    assert response.headers["x-request-id"] == body["error"]["request_id"]


def test_llm_rate_limit_error_contract() -> None:
    """Rate limiting (HTTP 429 upstream) is its own error class, not a
    generic 503: clients can back off and retry instead of giving up."""
    exc = LLMRateLimitError()
    assert isinstance(exc, AppError)
    assert exc.status_code == 503
    assert exc.code == "LLM_RATE_LIMITED"
    assert "rate limiting" in exc.message


def test_llm_timeout_error_contract() -> None:
    exc = LLMTimeoutError()
    assert isinstance(exc, AppError)
    assert exc.status_code == 504
    assert exc.code == "LLM_TIMEOUT"
    assert "timed out" in exc.message


def test_llm_rate_limit_error_uses_error_envelope(routed_app: FastAPI) -> None:
    client = TestClient(routed_app)
    response = client.get("/api/v1/_test/rate-limit")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "LLM_RATE_LIMITED"
    assert body["error"]["request_id"]


def test_llm_timeout_error_uses_error_envelope(routed_app: FastAPI) -> None:
    client = TestClient(routed_app)
    response = client.get("/api/v1/_test/timeout")
    assert response.status_code == 504
    body = response.json()
    assert body["error"]["code"] == "LLM_TIMEOUT"
    assert body["error"]["request_id"]


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["request_id"]


def test_error_response_carries_request_id_header(routed_app: FastAPI) -> None:
    client = TestClient(routed_app)
    response = client.get(
        "/api/v1/_test/app-error", headers={"X-Request-ID": "error-correlation-1"}
    )
    assert response.headers["x-request-id"] == "error-correlation-1"
    assert response.json()["error"]["request_id"] == "error-correlation-1"
