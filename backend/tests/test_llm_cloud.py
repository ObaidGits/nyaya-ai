"""Cloud LLM provider tests (OpenAI-compatible + Gemini; D-034/D-080).

HTTP is mocked with httpx.MockTransport — no network, no keys. Pins the
common interface (generate/stream/metadata/probe), error normalization to
503 LLM_PROVIDER_UNAVAILABLE, the classified health states (D-090 brain
status contract), and registry wiring for every supported provider name.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.core.config import Settings
from app.core.errors import AppError
from app.domain.models import MessageRole
from app.llm.base import ChatMessage, GenerationRequest, ProviderHealthState
from app.llm.gemini import GeminiProvider
from app.llm.openai_compat import PROFILES, OpenAICompatibleProvider
from app.llm.registry import create_default_registry


def _request(content: str = "hi") -> GenerationRequest:
    return GenerationRequest(messages=[ChatMessage(role=MessageRole.USER, content=content)])


class _HttpxShim:
    """Real httpx, except AsyncClient builds over a MockTransport."""

    def __init__(self, handler: Any) -> None:
        self._handler = handler

    def __getattr__(self, name: str) -> Any:
        return getattr(httpx, name)

    def AsyncClient(self, *args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(self._handler)
        return httpx.AsyncClient(*args, **kwargs)


def _mock(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    import app.llm.gemini as gemini_module
    import app.llm.openai_compat as compat_module

    monkeypatch.setattr(compat_module, "httpx", _HttpxShim(handler))
    monkeypatch.setattr(gemini_module, "httpx", _HttpxShim(handler))


class TestOpenAICompatible:
    def _provider(self, **overrides: Any) -> OpenAICompatibleProvider:
        kwargs: dict[str, Any] = {
            "provider": "openai",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "api_key": "sk-test",
        }
        kwargs.update(overrides)
        return OpenAICompatibleProvider(**kwargs)

    @pytest.mark.asyncio
    async def test_generate_parses_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/chat/completions"
            assert request.headers["Authorization"] == "Bearer sk-test"
            body = json.loads(request.content)
            assert body["model"] == "gpt-4o-mini"
            assert body["temperature"] == 0.2
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Section 103 answer."}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                },
            )

        _mock(monkeypatch, handler)
        result = await self._provider().generate(_request())
        assert result.text == "Section 103 answer."
        assert result.prompt_tokens == 10

    @pytest.mark.asyncio
    async def test_stream_yields_deltas(self, monkeypatch: pytest.MonkeyPatch) -> None:
        lines = (
            b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n'
            b'data: {"choices": [{"delta": {"content": " there"}}]}\n\n'
            b"data: [DONE]\n\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=lines, headers={"content-type": "text/event-stream"})

        _mock(monkeypatch, handler)
        chunks = [chunk async for chunk in self._provider().stream(_request())]
        assert chunks == ["Hello", " there"]

    @pytest.mark.asyncio
    async def test_http_error_normalized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        _mock(monkeypatch, handler)
        with pytest.raises(AppError) as excinfo:
            await self._provider().generate(_request())
        assert excinfo.value.status_code == 503
        assert excinfo.value.code == "LLM_PROVIDER_UNAVAILABLE"
        assert "sk-test" not in excinfo.value.message

    @pytest.mark.asyncio
    async def test_missing_key_fails_closed(self) -> None:
        provider = self._provider(api_key="")
        with pytest.raises(AppError) as excinfo:
            await provider.generate(_request())
        assert excinfo.value.status_code == 503

    def test_metadata(self) -> None:
        meta = self._provider().metadata()
        assert meta.provider == "openai"
        assert meta.model == "gpt-4o-mini"
        assert meta.supports_streaming is True

    @pytest.mark.asyncio
    async def test_health_check_rejects_4xx(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 401 (bad key) is NOT reachable — the console must not report
        success for a provider whose credentials are wrong."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401)

        _mock(monkeypatch, handler)
        assert await self._provider().health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_ok_on_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": []})

        _mock(monkeypatch, handler)
        assert await self._provider().health_check() is True


class TestGemini:
    def _provider(self, **overrides: Any) -> GeminiProvider:
        kwargs: dict[str, Any] = {"api_key": "gem-key", "model": "gemini-2.0-flash"}
        kwargs.update(overrides)
        return GeminiProvider(**kwargs)

    @pytest.mark.asyncio
    async def test_generate_parses_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "generateContent" in request.url.path
            assert request.url.params["key"] == "gem-key"
            return httpx.Response(
                200,
                json={
                    "candidates": [{"content": {"parts": [{"text": "BNS answer."}]}}],
                    "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3},
                },
            )

        _mock(monkeypatch, handler)
        result = await self._provider().generate(_request())
        assert result.text == "BNS answer."
        assert result.completion_tokens == 3

    @pytest.mark.asyncio
    async def test_error_normalized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403)

        _mock(monkeypatch, handler)
        with pytest.raises(AppError) as excinfo:
            await self._provider().generate(_request())
        assert excinfo.value.code == "LLM_PROVIDER_UNAVAILABLE"
        assert "gem-key" not in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_health_check_rejects_4xx(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 403 from Google (bad/missing key) is NOT reachable."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403)

        _mock(monkeypatch, handler)
        assert await self._provider().health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_ok_on_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"models": []})

        _mock(monkeypatch, handler)
        assert await self._provider().health_check() is True


class TestProbe:
    """Classified probe states (D-090): the brain indicator must distinguish
    missing config, rejected credentials, unreachable endpoints and a model
    the provider does not offer."""

    @pytest.mark.asyncio
    async def test_missing_key_is_not_configured(self) -> None:
        provider = OpenAICompatibleProvider(
            provider="grok", base_url="https://api.x.ai/v1", model="grok-4.6", api_key=""
        )
        health = await provider.probe()
        assert health.state is ProviderHealthState.NOT_CONFIGURED
        assert "API key" in health.detail

    @pytest.mark.asyncio
    async def test_missing_model_is_not_configured(self) -> None:
        provider = OpenAICompatibleProvider(
            provider="grok", base_url="https://api.x.ai/v1", model="", api_key="k"
        )
        health = await provider.probe()
        assert health.state is ProviderHealthState.NOT_CONFIGURED

    @pytest.mark.asyncio
    async def test_rejected_key_is_invalid_configuration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401)

        _mock(monkeypatch, handler)
        provider = OpenAICompatibleProvider(
            provider="grok", base_url="https://api.x.ai/v1", model="grok-4.6", api_key="bad"
        )
        health = await provider.probe()
        assert health.state is ProviderHealthState.INVALID_CONFIGURATION
        assert "401" in health.detail

    @pytest.mark.asyncio
    async def test_network_error_is_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route")

        _mock(monkeypatch, handler)
        provider = OpenAICompatibleProvider(
            provider="grok", base_url="https://api.x.ai/v1", model="grok-4.6", api_key="k"
        )
        health = await provider.probe()
        assert health.state is ProviderHealthState.UNAVAILABLE

    @pytest.mark.asyncio
    async def test_model_not_offered_is_degraded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"id": "grok-4.6"}]})

        _mock(monkeypatch, handler)
        provider = OpenAICompatibleProvider(
            provider="grok", base_url="https://api.x.ai/v1", model="grok-9", api_key="k"
        )
        health = await provider.probe()
        assert health.state is ProviderHealthState.DEGRADED
        assert "grok-9" in health.detail

    @pytest.mark.asyncio
    async def test_healthy_when_reachable_and_model_offered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/models"
            assert request.headers["Authorization"] == "Bearer k"
            return httpx.Response(200, json={"data": [{"id": "grok-4.6"}]})

        _mock(monkeypatch, handler)
        provider = OpenAICompatibleProvider(
            provider="grok", base_url="https://api.x.ai/v1", model="grok-4.6", api_key="k"
        )
        health = await provider.probe()
        assert health.state is ProviderHealthState.HEALTHY

    @pytest.mark.asyncio
    async def test_gemini_bad_key_is_invalid_configuration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Google answers 400 (not 401) for a missing/invalid key — still
        INVALID_CONFIGURATION, never healthy."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400)

        _mock(monkeypatch, handler)
        provider = GeminiProvider(api_key="bad", model="gemini-2.0-flash")
        health = await provider.probe()
        assert health.state is ProviderHealthState.INVALID_CONFIGURATION

    @pytest.mark.asyncio
    async def test_gemini_missing_key_is_not_configured(self) -> None:
        provider = GeminiProvider(api_key="", model="gemini-2.0-flash")
        health = await provider.probe()
        assert health.state is ProviderHealthState.NOT_CONFIGURED


class TestProviderDefaults:
    """Every built-in provider has its documented official endpoint (D-090);
    URLs verified against the providers' current API docs."""

    def test_profiles_carry_official_urls(self) -> None:
        assert PROFILES["openai"][0] == "https://api.openai.com/v1"
        assert PROFILES["grok"][0] == "https://api.x.ai/v1"
        assert PROFILES["groq"][0] == "https://api.groq.com/openai/v1"
        assert PROFILES["openrouter"][0] == "https://openrouter.ai/api/v1"
        # Only the generic profile requires a manually supplied URL.
        assert PROFILES["openai-compatible"][0] == ""

    def test_blank_base_url_falls_back_to_profile_default(self) -> None:
        """A blank LLM_BASE_URL must resolve to the provider's official
        endpoint — the admin must never have to type a well-known URL."""
        settings = Settings(
            _env_file=None,
            llm_provider="groq",
            llm_base_url="",
            llm_model="openai/gpt-oss-120b",
            llm_api_key="k",
        )
        provider = create_default_registry().create("groq", settings)
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.metadata().model == "openai/gpt-oss-120b"
        assert provider._base_url == "https://api.groq.com/openai/v1"

    def test_explicit_base_url_overrides_default(self) -> None:
        settings = Settings(
            _env_file=None,
            llm_provider="openai",
            llm_base_url="https://gateway.internal/v1",
            llm_model="gpt-4o-mini",
            llm_api_key="k",
        )
        provider = create_default_registry().create("openai", settings)
        assert provider._base_url == "https://gateway.internal/v1"


class TestRegistry:
    def test_all_providers_registered(self) -> None:
        registry = create_default_registry()
        assert set(registry.available()) == {
            "ollama",
            "openai",
            "gemini",
            "grok",
            "groq",
            "openrouter",
            "openai-compatible",
        }

    @pytest.mark.parametrize(
        ("provider", "expected_class"),
        [
            ("openai", OpenAICompatibleProvider),
            ("grok", OpenAICompatibleProvider),
            ("groq", OpenAICompatibleProvider),
            ("openrouter", OpenAICompatibleProvider),
            ("openai-compatible", OpenAICompatibleProvider),
            ("gemini", GeminiProvider),
        ],
    )
    def test_factory_creates_provider(self, provider: str, expected_class: type) -> None:
        registry = create_default_registry()
        settings = Settings(
            _env_file=None,
            llm_provider=provider,
            llm_api_key="k",
            llm_model="m",
            llm_base_url="https://gw.example/v1",
        )
        instance = registry.create(provider, settings)
        assert isinstance(instance, expected_class)

    def test_ollama_stays_default(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.llm_provider == "ollama"
