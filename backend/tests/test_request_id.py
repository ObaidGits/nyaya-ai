"""Request ID tests (REQUIREMENTS.md D-052)."""

import logging
import uuid

import pytest
from fastapi.testclient import TestClient

UUID_LENGTH = 36  # canonical str(uuid4())


def _is_generated_request_id(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return len(value) == UUID_LENGTH


def test_request_id_generated_when_missing(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    request_id = response.headers["x-request-id"]
    assert _is_generated_request_id(request_id)


def test_supplied_request_id_is_preserved(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"X-Request-ID": "client-id-42"})
    assert response.headers["x-request-id"] == "client-id-42"


def test_unsafe_request_id_is_replaced(client: TestClient) -> None:
    malicious = "../../etc/passwd"
    response = client.get("/api/v1/health", headers={"X-Request-ID": malicious})
    request_id = response.headers["x-request-id"]
    assert request_id != malicious
    assert _is_generated_request_id(request_id)


def test_overlong_request_id_is_replaced(client: TestClient) -> None:
    overlong = "a" * 65
    response = client.get("/api/v1/health", headers={"X-Request-ID": overlong})
    request_id = response.headers["x-request-id"]
    assert request_id != overlong
    assert _is_generated_request_id(request_id)


def test_request_id_correlates_logs(app, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="nyaya.access")
    request_id = "log-correlation-id-1"
    with TestClient(app) as test_client:
        response = test_client.get("/api/v1/health", headers={"X-Request-ID": request_id})
    assert response.status_code == 200

    matching = [
        record for record in caplog.records if getattr(record, "request_id", None) == request_id
    ]
    assert matching, "expected an access log record carrying the request ID"
    completed = [record for record in matching if record.getMessage() == "request completed"]
    assert completed
    assert completed[0].status_code == 200
    assert completed[0].path == "/api/v1/health"
    assert completed[0].method == "GET"
