"""LLM provider abstraction and registry tests (REQUIREMENTS.md LLM-001..LLM-003).

These tests verify the Phase 1 boundary only: the abstract provider interface,
the registry and the dependency-injection seam. No concrete provider ships in
Phase 1, so a test double stands in for one.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

import pytest
from app.api.deps import get_llm_provider
from app.core.config import Settings
from app.core.errors import AppError
from app.domain.models import MessageRole
from app.llm.base import (
    ChatMessage,
    GenerationRequest,
    GenerationResult,
    LLMProvider,
    ProviderMetadata,
)
from app.llm.registry import (
    ProviderRegistry,
    UnknownProviderError,
    create_default_registry,
)
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


class StubProvider(LLMProvider):
    """Minimal test double for the provider interface."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        return GenerationResult(text="stub generation", model=self.settings.llm_model)

    def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        yield "stub "
        yield "chunk"

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            provider="stub", model=self.settings.llm_model, supports_streaming=True
        )

    async def health_check(self) -> bool:
        return True


def _mount_provider_route(app: FastAPI) -> None:
    @app.get("/api/v1/_test/provider")
    async def _provider(
        provider: Annotated[LLMProvider, Depends(get_llm_provider)],
    ) -> dict[str, object]:
        return provider.metadata().model_dump()


def test_provider_interface_contract() -> None:
    """The test double satisfies the abstract interface (LLM-001)."""
    provider = StubProvider(Settings(_env_file=None))
    request = GenerationRequest(messages=[ChatMessage(role=MessageRole.USER, content="Hello")])
    result = asyncio.run(provider.generate(request))
    assert result.text == "stub generation"
    assert list(provider.stream(request)) == ["stub ", "chunk"]
    metadata = provider.metadata()
    assert metadata.provider == "stub"
    assert metadata.supports_streaming is True
    assert asyncio.run(provider.health_check()) is True


def test_registry_registers_and_creates_provider() -> None:
    registry = ProviderRegistry()
    registry.register("stub", StubProvider)
    assert registry.available() == ["stub"]
    provider = registry.create("stub", Settings(_env_file=None))
    assert isinstance(provider, StubProvider)


def test_registry_lists_providers_sorted() -> None:
    registry = ProviderRegistry()
    registry.register("zeta", StubProvider)
    registry.register("alpha", StubProvider)
    assert registry.available() == ["alpha", "zeta"]


def test_unknown_provider_raises_clear_error() -> None:
    registry = ProviderRegistry()
    registry.register("stub", StubProvider)
    with pytest.raises(UnknownProviderError) as excinfo:
        registry.create("ollama", Settings(_env_file=None))
    message = str(excinfo.value)
    assert "ollama" in message
    assert "stub" in message  # the error names the registered providers
    assert isinstance(excinfo.value, AppError)


def test_default_registry_registers_ollama() -> None:
    """Phase 4 registers the keyless Ollama provider (D-033); more may follow."""
    assert "ollama" in create_default_registry().available()


def test_dependency_injection_resolves_configured_provider() -> None:
    """The DI seam selects the provider from settings.llm_provider (LLM-003)."""
    from app.main import create_app

    settings = Settings(_env_file=None, llm_provider="stub")
    app = create_app(settings=settings)
    app.state.llm_registry.register("stub", StubProvider)
    _mount_provider_route(app)
    response = TestClient(app).get("/api/v1/_test/provider")
    assert response.status_code == 200
    assert response.json()["provider"] == "stub"


def test_unregistered_provider_yields_503(app: FastAPI, client: TestClient) -> None:
    """A provider name missing from the registry surfaces a 503."""
    app.state.settings = app.state.settings.model_copy(update={"llm_provider": "does-not-exist"})
    _mount_provider_route(app)
    response = client.get("/api/v1/_test/provider")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "LLM_PROVIDER_NOT_CONFIGURED"
