"""Cloud LLM provider tests (OpenAI-compatible + Gemini; D-034/D-080).

HTTP is mocked with httpx.MockTransport — no network, no keys. Pins the
common interface (generate/stream/metadata/health_check), error
normalization to 503 LLM_PROVIDER_UNAVAILABLE, and registry wiring for every
supported provider name.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.core.config import Settings
from app.core.errors import AppError
from app.domain.models import MessageRole
from app.llm.base import ChatMessage, GenerationRequest
from app.llm.gemini import GeminiProvider
from app.llm.openai_compat import OpenAICompatibleProvider
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


class TestRegistry:
    def test_all_providers_registered(self) -> None:
        registry = create_default_registry()
        assert set(registry.available()) == {
            "ollama",
            "openai",
            "gemini",
            "grok",
            "openrouter",
            "openai-compatible",
        }

    @pytest.mark.parametrize(
        ("provider", "expected_class"),
        [
            ("openai", OpenAICompatibleProvider),
            ("grok", OpenAICompatibleProvider),
            ("openrouter", OpenAICompatibleProvider),
            ("openai-compatible", OpenAICompatibleProvider),
            ("gemini", GeminiProvider),
        ],
    )
    def test_factory_creates_provider(self, provider: str, expected_class: type) -> None:
        registry = create_default_registry()
        settings = Settings(_env_file=None, llm_provider=provider, llm_api_key="k", llm_model="m")
        instance = registry.create(provider, settings)
        assert isinstance(instance, expected_class)

    def test_ollama_stays_default(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.llm_provider == "ollama"
