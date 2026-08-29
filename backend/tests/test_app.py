"""Application-level tests: import, startup, OpenAPI (REQUIREMENTS.md D-001, D-004, D-055)."""

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_application_imports() -> None:
    """The application module exposes a constructed FastAPI application."""
    from app import main

    assert isinstance(main.app, FastAPI)


def test_application_starts(client: TestClient) -> None:
    """A request through the full ASGI stack succeeds."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_openapi_schema_generated(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "Nyaya API"
    assert schema["info"]["version"]
    assert "/api/v1/health" in schema["paths"]
    assert "/api/v1/health/ready" in schema["paths"]


def test_docs_served(client: TestClient) -> None:
    response = client.get("/docs")
    assert response.status_code == 200
