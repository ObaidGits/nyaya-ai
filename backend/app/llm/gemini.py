"""Google Gemini LLM provider (DECISIONS.md D-034/D-080).

Uses Gemini's ``generateContent`` REST API (``streamGenerateContent`` with
SSE for streaming) via the ``google`` Python SDK-compatible endpoints — plain
httpx keeps the dependency footprint minimal. Errors are normalized to the
application's 503 AppError; provider bodies never reach clients.
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
    ProviderMetadata,
)
from app.llm.sanitize import ReasoningStreamFilter, sanitize_answer_text

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiProviderError(AppError):
    status_code = 503
    code = "LLM_PROVIDER_UNAVAILABLE"


class GeminiProvider(LLMProvider):
    """Concrete provider for Google Gemini."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 120.0,
        temperature: float = 0.2,
        max_output_tokens: int | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens

    def _payload(self, request: GenerationRequest) -> dict[str, Any]:
        contents = [
            {
                "role": "model" if message.role.value == "assistant" else "user",
                "parts": [{"text": message.content}],
            }
            for message in request.messages
        ]
        generation_config: dict[str, Any] = {"temperature": self._temperature}
        if self._max_output_tokens is not None:
            generation_config["maxOutputTokens"] = self._max_output_tokens
        return {"contents": contents, "generationConfig": generation_config}

    def _require_config(self) -> None:
        if not self._api_key:
            raise GeminiProviderError(
                "The 'gemini' provider is selected but no API key is configured."
            )
        if not self._model:
            raise GeminiProviderError("The 'gemini' provider needs a model name.")

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self._require_config()
        url = f"{self._base_url}/models/{self._model}:generateContent"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    url,
                    params={"key": self._api_key},
                    json=self._payload(request),
                )
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError:
            logger.warning("gemini rejected request", extra={"error_type": "HTTPStatusError"})
            raise GeminiProviderError("The generation provider rejected the request.") from None
        except httpx.HTTPError as exc:
            logger.warning("gemini unreachable", extra={"error_type": type(exc).__name__})
            raise GeminiProviderError("The generation provider is currently unavailable.") from exc
        candidates = data.get("candidates") or [{}]
        parts = (candidates[0].get("content") or {}).get("parts") or []
        # Thought-summary parts (thought: true) are Gemini's reasoning
        # channel — never answer content. Only plain text parts count, and
        # wrapper-formatted reasoning inside them is stripped.
        text = "".join(str(part.get("text", "")) for part in parts if not part.get("thought"))
        usage = data.get("usageMetadata") or {}
        return GenerationResult(
            text=sanitize_answer_text(text),
            model=self._model,
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
        )

    async def _stream_chunks(self, request: GenerationRequest) -> AsyncIterator[str]:
        self._require_config()
        url = f"{self._base_url}/models/{self._model}:streamGenerateContent"
        try:
            async with (
                httpx.AsyncClient(timeout=self._timeout) as client,
                client.stream(
                    "POST",
                    url,
                    params={"key": self._api_key, "alt": "sse"},
                    json=self._payload(request),
                ) as response,
            ):
                response.raise_for_status()
                stream_filter = ReasoningStreamFilter()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:") :].strip()
                    if not payload:
                        continue
                    try:
                        chunk = json.loads(payload)
                    except ValueError:
                        continue
                    candidates = chunk.get("candidates") or [{}]
                    parts = (candidates[0].get("content") or {}).get("parts") or []
                    for part in parts:
                        if part.get("thought"):
                            continue  # reasoning channel, never answer text
                        token = part.get("text")
                        if token:
                            safe = stream_filter.push(str(token))
                            if safe:
                                yield safe
                tail = stream_filter.flush()
                if tail:
                    yield tail
        except httpx.HTTPError as exc:
            logger.warning("gemini streaming failed", extra={"error_type": type(exc).__name__})
            raise GeminiProviderError("The generation provider is currently unavailable.") from exc

    def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        return self._stream_chunks(request)

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(provider="gemini", model=self._model, supports_streaming=True)

    async def health_check(self) -> bool:
        if not self._api_key:
            return False
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(
                    f"{self._base_url}/models",
                    params={"key": self._api_key},
                )
                # 4xx means a bad/missing key or model — NOT reachable.
                return response.status_code == 200
        except httpx.HTTPError:
            return False


def create_gemini_provider(settings: Settings) -> LLMProvider:
    # Cap the timeout at 300 s so a bad setting cannot hang requests forever.
    return GeminiProvider(
        api_key=(settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""),
        model=settings.llm_model or DEFAULT_MODEL,
        base_url=settings.llm_base_url or DEFAULT_BASE_URL,
        timeout_seconds=min(settings.llm_timeout_seconds, 300.0),
        temperature=settings.llm_temperature,
        max_output_tokens=settings.llm_num_predict,
    )


__all__ = ["GeminiProvider", "create_gemini_provider"]
