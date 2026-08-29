"""Shared fixtures for the Phase 1 backend test suite."""

from collections.abc import Iterator

import pytest
from app.core.config import Settings
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def settings() -> Settings:
    """Hermetic settings that ignore .env files and ambient environment."""
    return Settings(_env_file=None)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    """Application built with test settings."""
    return create_app(settings=settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """Synchronous test client for the application."""
    with TestClient(app) as test_client:
        yield test_client
