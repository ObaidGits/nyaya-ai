"""Ollama LLM provider (DECISIONS.md D-033, REQUIREMENTS.md LLM-002).

Keyless local generation over the Ollama HTTP API (``/api/chat``). The
application never imports this module directly — it is registered in the
provider registry and selected via ``LLM_PROVIDER`` configuration, keeping
the provider replaceable (LLM-003).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from app.core.config import Settings
from app.core.errors import AppError
from app.llm.base import (
    GenerationRequest,
    GenerationResult,
    LLMProvider,
    ProviderMetadata,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "llama3.1:8b"


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


class OllamaProviderError(AppError):
    """The Ollama backend failed or is unreachable."""

    status_code = 503
    code = "LLM_PROVIDER_UNAVAILABLE"


class OllamaProvider(LLMProvider):
    """Concrete provider for a local Ollama server (D-033)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_seconds: float = 300.0,
        num_ctx: int | None = None,
        num_predict: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.num_ctx = num_ctx
        self.num_predict = num_predict

    def _payload(self, request: GenerationRequest, *, stream: bool) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in request.messages
            ],
            "stream": stream,
        }
        options: dict[str, int] = {}
        if self.num_ctx is not None:
            # Ollama silently truncates the prompt to its default context
            # window (~4k for most models). A grounded generation prompt
            # with 10 evidence chunks is routinely larger, which drops
            # evidence — and even the question — and yields garbage. Set
            # the window explicitly.
            options["num_ctx"] = self.num_ctx
        if self.num_predict is not None:
            # Bound the completion length: a small local model can ramble
            # indefinitely on grounded prompts, which turns into HTTP
            # timeouts mid-answer.
            options["num_predict"] = self.num_predict
        if options:
            payload["options"] = options
        return payload

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        url = f"{self.base_url}/api/chat"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, json=self._payload(request, stream=False))
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            logger.warning(
                "ollama generation failed",
                extra={"error_type": type(exc).__name__, "url": self.base_url},
            )
            raise OllamaProviderError("The generation provider is currently unavailable.") from exc
        message = data.get("message") or {}
        return GenerationResult(
            text=str(message.get("content", "")),
            model=self.model,
            prompt_tokens=_optional_int(data.get("prompt_eval_count")),
            completion_tokens=_optional_int(data.get("eval_count")),
        )

    async def _stream_chunks(self, request: GenerationRequest) -> AsyncIterator[str]:
        url = f"{self.base_url}/api/chat"
        try:
            async with (
                httpx.AsyncClient(timeout=self.timeout_seconds) as client,
                client.stream("POST", url, json=self._payload(request, stream=True)) as response,
            ):
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError:
                        continue
                    message = chunk.get("message") or {}
                    token = message.get("content")
                    if token:
                        yield str(token)
        except httpx.HTTPError as exc:
            logger.warning(
                "ollama streaming failed",
                extra={"error_type": type(exc).__name__, "url": self.base_url},
            )
            raise OllamaProviderError("The generation provider is currently unavailable.") from exc

    def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        return self._stream_chunks(request)

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(provider="ollama", model=self.model, supports_streaming=True)

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.status_code == 200
        except httpx.HTTPError:
            return False


def create_ollama_provider(settings: Settings) -> LLMProvider:
    """Factory registered in the default provider registry."""
    return OllamaProvider(
        base_url=settings.llm_base_url,
        model=settings.llm_model or DEFAULT_MODEL,
        timeout_seconds=settings.llm_timeout_seconds,
        num_ctx=settings.llm_num_ctx,
        num_predict=settings.llm_num_predict,
    )


__all__ = [
    "DEFAULT_MODEL",
    "OllamaProvider",
    "OllamaProviderError",
    "create_ollama_provider",
]
