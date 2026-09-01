"""OpenAI-compatible chat-completions LLM provider (DECISIONS.md D-034/D-080).

One implementation covers OpenAI, Grok (x.ai), OpenRouter and any
OpenAI-compatible gateway: they differ only in base URL, default model and
API key. Streaming uses SSE ``data:`` lines. Errors are normalized to the
application's 503 LLM_PROVIDER_UNAVAILABLE AppError; provider bodies are
never leaked to clients.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import AppError
from app.llm.base import (
    GenerationRequest,
    GenerationResult,
    LLMProvider,
    ProviderHealth,
    ProviderHealthState,
    ProviderMetadata,
)
from app.llm.sanitize import ReasoningStreamFilter, sanitize_answer_text

logger = logging.getLogger(__name__)


class CloudProviderError(AppError):
    status_code = 503
    code = "LLM_PROVIDER_UNAVAILABLE"


# name -> (default base url, default model, display name). URLs/models are
# the providers' documented official endpoints (verified against current
# docs): OpenAI https://api.openai.com/v1, xAI https://api.x.ai/v1,
# Groq https://api.groq.com/openai/v1, OpenRouter https://openrouter.ai/api/v1.
PROFILES: dict[str, tuple[str, str, str]] = {
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini", "OpenAI"),
    "openai-compatible": ("", "", "OpenAI-compatible"),
    "grok": ("https://api.x.ai/v1", "grok-4.6", "Grok (xAI)"),
    "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b", "Groq"),
    "openrouter": ("https://openrouter.ai/api/v1", "openai/gpt-4o-mini", "OpenRouter"),
}


class OpenAICompatibleProvider(LLMProvider):
    """Chat-completions provider for OpenAI-compatible endpoints."""

    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float = 120.0,
        temperature: float = 0.2,
        max_output_tokens: int | None = None,
        disable_reasoning: bool = False,
    ) -> None:
        self._provider = provider
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._disable_reasoning = disable_reasoning

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._provider == "openrouter":
            headers["HTTP-Referer"] = "https://nyaya.local"
            headers["X-Title"] = "Nyaya"
        return headers

    def _payload(self, request: GenerationRequest, *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in request.messages
            ],
            "temperature": self._temperature,
            "stream": stream,
        }
        if self._max_output_tokens is not None:
            payload["max_tokens"] = self._max_output_tokens
        # Native reasoning off-switch (opt-in): only sent to providers that
        # document the parameter — unknown fields break strict gateways, so
        # openai-compatible/grok/openrouter never receive it.
        if self._disable_reasoning and self._provider == "openai":
            payload["reasoning_effort"] = "none"
        return payload

    def _require_key(self) -> None:
        if not self._api_key:
            raise CloudProviderError(
                f"The '{self._provider}' provider is selected but no API key is configured."
            )

    def _require_endpoint(self) -> None:
        if not self._base_url or not self._model:
            raise CloudProviderError(
                f"The '{self._provider}' provider needs a base URL and a model name."
            )

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self._require_key()
        self._require_endpoint()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=self._payload(request, stream=False),
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError:
            logger.warning(
                "cloud llm rejected request",
                extra={"provider": self._provider, "error_type": "HTTPStatusError"},
            )
            raise CloudProviderError("The generation provider rejected the request.") from None
        except httpx.HTTPError as exc:
            logger.warning(
                "cloud llm unreachable",
                extra={"provider": self._provider, "error_type": type(exc).__name__},
            )
            raise CloudProviderError("The generation provider is currently unavailable.") from exc
        # Only the content field is the answer. Reasoning fields
        # (reasoning / reasoning_content / reasoning_details) are never read;
        # reasoning wrappers inside content itself are stripped here.
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = data.get("usage") or {}
        return GenerationResult(
            text=sanitize_answer_text(str(message.get("content") or "")),
            model=self._model,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    async def _stream_chunks(self, request: GenerationRequest) -> AsyncIterator[str]:
        self._require_key()
        self._require_endpoint()
        try:
            async with (
                httpx.AsyncClient(timeout=self._timeout) as client,
                client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=self._payload(request, stream=True),
                ) as response,
            ):
                response.raise_for_status()
                # Streaming reasoning isolation: only delta.content passes,
                # and it passes through the wrapper filter so <think> blocks
                # streamed in content are suppressed even across chunk
                # boundaries.
                stream_filter = ReasoningStreamFilter()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:") :].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(payload)
                    except ValueError:
                        continue
                    delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                    token = delta.get("content")
                    if token:
                        safe = stream_filter.push(str(token))
                        if safe:
                            yield safe
                tail = stream_filter.flush()
                if tail:
                    yield tail
        except httpx.HTTPError as exc:
            logger.warning(
                "cloud llm streaming failed",
                extra={"provider": self._provider, "error_type": type(exc).__name__},
            )
            raise CloudProviderError("The generation provider is currently unavailable.") from exc

    def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        return self._stream_chunks(request)

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(provider=self._provider, model=self._model, supports_streaming=True)

    async def health_check(self) -> bool:
        if not self._api_key or not self._base_url:
            return False
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(f"{self._base_url}/models", headers=self._headers())
                # 4xx means a bad/missing key or model — NOT reachable.
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def probe(self) -> ProviderHealth:
        """Classified health (brain status contract): distinguishes missing
        configuration, rejected credentials, an unreachable endpoint and a
        model the provider does not offer."""
        if not self._api_key:
            return ProviderHealth(
                state=ProviderHealthState.NOT_CONFIGURED,
                provider=self._provider,
                model=self._model or None,
                detail=f"The '{self._provider}' provider is selected but no API key is configured.",
            )
        if not self._base_url or not self._model:
            return ProviderHealth(
                state=ProviderHealthState.NOT_CONFIGURED,
                provider=self._provider,
                model=self._model or None,
                detail=f"The '{self._provider}' provider needs a base URL and a model name.",
            )
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(f"{self._base_url}/models", headers=self._headers())
        except httpx.HTTPError:
            return ProviderHealth(
                state=ProviderHealthState.UNAVAILABLE,
                provider=self._provider,
                model=self._model,
                detail="The provider endpoint is unreachable (network error or timeout).",
            )
        status = response.status_code
        if status in (400, 401, 403):
            return ProviderHealth(
                state=ProviderHealthState.INVALID_CONFIGURATION,
                provider=self._provider,
                model=self._model,
                detail=f"The provider rejected the API key (HTTP {status}).",
            )
        if status == 404:
            return ProviderHealth(
                state=ProviderHealthState.INVALID_CONFIGURATION,
                provider=self._provider,
                model=self._model,
                detail="No model-listing endpoint at this URL (HTTP 404) — check the base URL.",
            )
        if status != 200:
            return ProviderHealth(
                state=ProviderHealthState.UNAVAILABLE,
                provider=self._provider,
                model=self._model,
                detail=f"The provider returned HTTP {status} while listing models.",
            )
        # Authenticated and reachable — verify the configured model is offered.
        try:
            offered = sorted(
                str(entry.get("id")) for entry in response.json().get("data", []) if entry.get("id")
            )
        except ValueError:
            offered = None
        if offered is not None and self._model not in offered:
            return ProviderHealth(
                state=ProviderHealthState.DEGRADED,
                provider=self._provider,
                model=self._model,
                detail=(
                    f"The provider is reachable, but model '{self._model}' is not in its "
                    "model list."
                ),
            )
        return ProviderHealth(
            state=ProviderHealthState.HEALTHY,
            provider=self._provider,
            model=self._model,
            detail="reachable and model available" if offered is not None else "reachable",
        )


def create_openai_provider(settings: Settings) -> LLMProvider:
    return _create_compat(settings, "openai")


def create_openai_compatible_provider(settings: Settings) -> LLMProvider:
    return _create_compat(settings, "openai-compatible")


def create_grok_provider(settings: Settings) -> LLMProvider:
    return _create_compat(settings, "grok")


def create_groq_provider(settings: Settings) -> LLMProvider:
    return _create_compat(settings, "groq")


def create_openrouter_provider(settings: Settings) -> LLMProvider:
    return _create_compat(settings, "openrouter")


def _create_compat(settings: Settings, provider: str) -> LLMProvider:
    default_url, default_model, _ = PROFILES[provider]
    # Cap the timeout at 300 s so a bad setting cannot hang requests forever.
    return OpenAICompatibleProvider(
        provider=provider,
        base_url=settings.llm_base_url or default_url,
        model=settings.llm_model or default_model,
        api_key=(settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""),
        timeout_seconds=min(settings.llm_timeout_seconds, 300.0),
        temperature=settings.llm_temperature,
        max_output_tokens=settings.llm_num_predict,
        disable_reasoning=settings.llm_disable_reasoning,
    )


__all__ = [
    "PROFILES",
    "OpenAICompatibleProvider",
    "create_grok_provider",
    "create_groq_provider",
    "create_openai_compatible_provider",
    "create_openai_provider",
    "create_openrouter_provider",
]
