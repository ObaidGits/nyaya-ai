"""Google Gemini LLM provider (DECISIONS.md D-034/D-080).

Uses Gemini's ``generateContent`` REST API (``streamGenerateContent`` with
SSE for streaming) via the ``google`` Python SDK-compatible endpoints — plain
httpx keeps the dependency footprint minimal. Transient failures (HTTP 429
rate limit, 5xx — Google serves 503 when overloaded) are retried with
bounded backoff honoring ``Retry-After``; remaining errors are normalized
to typed 503 AppErrors. Provider bodies never reach clients.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import AppError, LLMRateLimitError, LLMTimeoutError
from app.llm.base import (
    RATE_LIMIT_MAX_RETRIES,
    SERVER_ERROR_MAX_RETRIES,
    GenerationRequest,
    GenerationResult,
    LLMProvider,
    ProviderHealth,
    ProviderHealthState,
    ProviderMetadata,
    retry_delay,
    retry_sleep,
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
        # Bounded retry for transient failures only: HTTP 429 (Google's rate
        # limit, up to RATE_LIMIT_MAX_RETRIES retries) and 5xx (Google serves
        # 503 when overloaded, one retry). Each HTTP attempt gets the full
        # request timeout and every sleep is capped, so added latency stays
        # small against the timeout budget.
        rate_limit_retries = 0
        server_error_retries = 0
        while True:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        url,
                        params={"key": self._api_key},
                        json=self._payload(request),
                    )
                    response.raise_for_status()
                    data = response.json()
                    break
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 429 and rate_limit_retries < RATE_LIMIT_MAX_RETRIES:
                    rate_limit_retries += 1
                    await retry_sleep(
                        retry_delay(
                            rate_limit_retries,
                            exc.response.headers.get("Retry-After"),
                        )
                    )
                    continue
                if status >= 500 and server_error_retries < SERVER_ERROR_MAX_RETRIES:
                    server_error_retries += 1
                    await retry_sleep(retry_delay(server_error_retries))
                    continue
                if status == 429:
                    logger.warning("gemini rate limited", extra={"status": status})
                    raise LLMRateLimitError() from None
                if 400 <= status < 500:
                    # Google answers 400 (not 401) for a bad key — never
                    # transient.
                    logger.warning("gemini rejected request", extra={"status": status})
                    raise GeminiProviderError(
                        "The generation provider rejected the request."
                    ) from None
                logger.warning("gemini server error", extra={"status": status})
                raise GeminiProviderError(
                    "The generation provider is currently unavailable."
                ) from None
            except httpx.TimeoutException as exc:
                logger.warning("gemini timed out", extra={"error_type": type(exc).__name__})
                raise LLMTimeoutError() from exc
            except httpx.HTTPError as exc:
                logger.warning("gemini unreachable", extra={"error_type": type(exc).__name__})
                raise GeminiProviderError(
                    "The generation provider is currently unavailable."
                ) from exc
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
        # Same bounded retry as generate(); a retry is only possible BEFORE
        # the first token is yielded — once the consumer has received
        # anything, the SSE stream is committed and replaying the request
        # would duplicate output.
        rate_limit_retries = 0
        server_error_retries = 0
        yielded = False
        while True:
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
                                    yielded = True
                                    yield safe
                    tail = stream_filter.flush()
                    if tail:
                        yielded = True
                        yield tail
                break
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if not yielded and status == 429 and rate_limit_retries < RATE_LIMIT_MAX_RETRIES:
                    rate_limit_retries += 1
                    await retry_sleep(
                        retry_delay(
                            rate_limit_retries,
                            exc.response.headers.get("Retry-After"),
                        )
                    )
                    continue
                if (
                    not yielded
                    and status >= 500
                    and server_error_retries < SERVER_ERROR_MAX_RETRIES
                ):
                    server_error_retries += 1
                    await retry_sleep(retry_delay(server_error_retries))
                    continue
                if status == 429:
                    logger.warning("gemini rate limited (stream)", extra={"status": status})
                    raise LLMRateLimitError() from None
                if 400 <= status < 500:
                    logger.warning("gemini rejected request (stream)", extra={"status": status})
                    raise GeminiProviderError(
                        "The generation provider rejected the request."
                    ) from None
                logger.warning("gemini streaming failed", extra={"status": status})
                raise GeminiProviderError(
                    "The generation provider is currently unavailable."
                ) from None
            except httpx.TimeoutException as exc:
                logger.warning(
                    "gemini timed out (stream)", extra={"error_type": type(exc).__name__}
                )
                raise LLMTimeoutError() from exc
            except httpx.HTTPError as exc:
                logger.warning("gemini streaming failed", extra={"error_type": type(exc).__name__})
                raise GeminiProviderError(
                    "The generation provider is currently unavailable."
                ) from exc

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

    async def probe(self, *, verify_chat: bool = False) -> ProviderHealth:
        """Classified health (brain status contract).

        ``verify_chat=True`` (D-096) follows the model-list check with one
        tiny ``generateContent`` call, so a model that cannot answer fails
        at configuration time.
        """
        if not self._api_key:
            return ProviderHealth(
                state=ProviderHealthState.NOT_CONFIGURED,
                provider="gemini",
                model=self._model or None,
                detail="The 'gemini' provider is selected but no API key is configured.",
            )
        if not self._model:
            return ProviderHealth(
                state=ProviderHealthState.NOT_CONFIGURED,
                provider="gemini",
                detail="The 'gemini' provider needs a model name.",
            )
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(
                    f"{self._base_url}/models",
                    params={"key": self._api_key},
                )
        except httpx.HTTPError:
            return ProviderHealth(
                state=ProviderHealthState.UNAVAILABLE,
                provider="gemini",
                model=self._model,
                detail="The Gemini endpoint is unreachable (network error or timeout).",
            )
        status = response.status_code
        if status in (400, 401, 403):
            # Google returns 400 (not 401/403) for a missing/invalid key.
            return ProviderHealth(
                state=ProviderHealthState.INVALID_CONFIGURATION,
                provider="gemini",
                model=self._model,
                detail=f"Google rejected the API key (HTTP {status}).",
            )
        if status != 200:
            return ProviderHealth(
                state=ProviderHealthState.UNAVAILABLE,
                provider="gemini",
                model=self._model,
                detail=f"Google returned HTTP {status} while listing models.",
            )
        try:
            models = [
                str(entry.get("name", "")).removeprefix("models/")
                for entry in response.json().get("models", [])
            ]
        except ValueError:
            models = None
        if models is not None and self._model not in models:
            return ProviderHealth(
                state=ProviderHealthState.DEGRADED,
                provider="gemini",
                model=self._model,
                detail=f"Google is reachable, but model '{self._model}' is not offered.",
            )
        if not verify_chat:
            return ProviderHealth(
                state=ProviderHealthState.HEALTHY,
                provider="gemini",
                model=self._model,
                detail="reachable and model available" if models is not None else "reachable",
            )
        return await self._verify_chat()

    _CHAT_VERIFY_PROMPT = "Reply with the single word OK."

    async def _verify_chat(self) -> ProviderHealth:
        """One tiny ``generateContent`` call (D-096). No ``maxOutputTokens``
        cap: thinking models (Gemini 2.5…) spend a small cap on hidden
        thought and return no visible text."""
        payload = {
            "contents": [{"parts": [{"text": self._CHAT_VERIFY_PROMPT}]}],
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{self._base_url}/models/{self._model}:generateContent",
                    params={"key": self._api_key},
                    json=payload,
                )
        except httpx.HTTPError:
            return ProviderHealth(
                state=ProviderHealthState.UNAVAILABLE,
                provider="gemini",
                model=self._model,
                detail="The Gemini endpoint is unreachable during chat verification "
                "(network error or timeout).",
                chat_verified=False,
            )
        status = response.status_code
        if status == 200:
            try:
                text = str(response.json()["candidates"][0]["content"]["parts"][0]["text"] or "")
            except (ValueError, KeyError, IndexError, TypeError):
                text = ""
            if text.strip():
                return ProviderHealth(
                    state=ProviderHealthState.HEALTHY,
                    provider="gemini",
                    model=self._model,
                    detail="chat verified — the model answered a test prompt",
                    chat_verified=True,
                )
            return ProviderHealth(
                state=ProviderHealthState.DEGRADED,
                provider="gemini",
                model=self._model,
                detail="The model returned an empty response to a test chat request.",
                chat_verified=False,
            )
        if status == 400:
            # Google answers 400 both for a bad key and a bad request; the
            # key was already accepted by the model list, so this points at
            # the model or request shape.
            return ProviderHealth(
                state=ProviderHealthState.INVALID_CONFIGURATION,
                provider="gemini",
                model=self._model,
                detail=(
                    f"Google rejected a chat request for model '{self._model}' "
                    "(HTTP 400) — the model may not be a generative chat model."
                ),
                chat_verified=False,
            )
        if status == 404:
            return ProviderHealth(
                state=ProviderHealthState.INVALID_CONFIGURATION,
                provider="gemini",
                model=self._model,
                detail=f"Model '{self._model}' was not found for a chat request (HTTP 404).",
                chat_verified=False,
            )
        if status == 429:
            return ProviderHealth(
                state=ProviderHealthState.DEGRADED,
                provider="gemini",
                model=self._model,
                detail="Google is rate-limiting requests (HTTP 429) — reachable, "
                "but chat could not be verified; retry the test shortly.",
                chat_verified=None,
            )
        return ProviderHealth(
            state=ProviderHealthState.UNAVAILABLE,
            provider="gemini",
            model=self._model,
            detail=f"Google returned HTTP {status} during chat verification.",
            chat_verified=False,
        )


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
