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
from app.core.errors import AppError, LLMTimeoutError
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
        disable_reasoning: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.num_ctx = num_ctx
        self.num_predict = num_predict
        self.disable_reasoning = disable_reasoning

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
        # Opt-in think disable (Ollama >= 0.9 native switch). Only sent when
        # explicitly configured: older Ollama servers reject unknown fields.
        if self.disable_reasoning:
            payload["think"] = False
        return payload

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        url = f"{self.base_url}/api/chat"
        # No retry, deliberately: Ollama is a local single-instance server,
        # so a connection failure or 5xx is a deployment problem (server
        # down, model not pulled), not transient upstream load — retrying
        # only delays the error. Cloud providers retry in their own layers.
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, json=self._payload(request, stream=False))
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            # A timeout is distinguishable from "unavailable": the server
            # was reachable but too slow (context too large, model too big).
            logger.warning(
                "ollama generation timed out",
                extra={"error_type": type(exc).__name__, "url": self.base_url},
            )
            raise LLMTimeoutError() from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "ollama generation failed",
                extra={"error_type": type(exc).__name__, "url": self.base_url},
            )
            raise OllamaProviderError("The generation provider is currently unavailable.") from exc
        # Only message.content is the answer; message.thinking (Ollama's
        # native reasoning field) is never read. Wrapper-formatted reasoning
        # inside content is stripped.
        message = data.get("message") or {}
        return GenerationResult(
            text=sanitize_answer_text(str(message.get("content", ""))),
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
                stream_filter = ReasoningStreamFilter()
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
                        safe = stream_filter.push(str(token))
                        if safe:
                            yield safe
                tail = stream_filter.flush()
                if tail:
                    yield tail
        except httpx.TimeoutException as exc:
            logger.warning(
                "ollama streaming timed out",
                extra={"error_type": type(exc).__name__, "url": self.base_url},
            )
            raise LLMTimeoutError() from exc
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

    async def probe(self) -> ProviderHealth:
        """Classified health (brain status contract): the model must actually
        be pulled, not merely the server reachable."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
        except httpx.HTTPError:
            return ProviderHealth(
                state=ProviderHealthState.UNAVAILABLE,
                provider="ollama",
                model=self.model,
                detail="The Ollama server is unreachable.",
            )
        if response.status_code != 200:
            return ProviderHealth(
                state=ProviderHealthState.UNAVAILABLE,
                provider="ollama",
                model=self.model,
                detail=f"The Ollama server returned HTTP {response.status_code}.",
            )
        try:
            tags = response.json().get("models") or []
        except ValueError:
            return ProviderHealth(
                state=ProviderHealthState.UNAVAILABLE,
                provider="ollama",
                model=self.model,
                detail="The Ollama server returned invalid JSON for /api/tags.",
            )
        names = {str(tag.get("name", "")) for tag in tags}
        names |= {str(tag.get("model", "")) for tag in tags}
        if self.model in names or any(name.startswith(f"{self.model}:") for name in names):
            return ProviderHealth(
                state=ProviderHealthState.HEALTHY,
                provider="ollama",
                model=self.model,
                detail="reachable and model available",
            )
        return ProviderHealth(
            state=ProviderHealthState.DEGRADED,
            provider="ollama",
            model=self.model,
            detail=f"Ollama is reachable, but model '{self.model}' is not pulled.",
        )


def create_ollama_provider(settings: Settings) -> LLMProvider:
    """Factory registered in the default provider registry."""
    return OllamaProvider(
        base_url=settings.llm_base_url,
        model=settings.llm_model or DEFAULT_MODEL,
        timeout_seconds=settings.llm_timeout_seconds,
        num_ctx=settings.llm_num_ctx,
        num_predict=settings.llm_num_predict,
        disable_reasoning=settings.llm_disable_reasoning,
    )


__all__ = [
    "DEFAULT_MODEL",
    "OllamaProvider",
    "OllamaProviderError",
    "create_ollama_provider",
]
