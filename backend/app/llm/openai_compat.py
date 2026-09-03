"""OpenAI-compatible chat-completions LLM provider (DECISIONS.md D-034/D-080).

One implementation covers OpenAI, Grok (x.ai), OpenRouter and any
OpenAI-compatible gateway: they differ only in base URL, default model and
API key. Streaming uses SSE ``data:`` lines. Transient failures (HTTP 429
rate limit, 5xx) are retried with bounded backoff honoring ``Retry-After``;
remaining errors are normalized to typed 503 AppErrors (LLM_RATE_LIMITED,
LLM_TIMEOUT, LLM_PROVIDER_UNAVAILABLE). Provider bodies are never leaked
to clients.
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


class CloudProviderError(AppError):
    status_code = 503
    code = "LLM_PROVIDER_UNAVAILABLE"

    #: True when the provider answered a definitive "no" (auth/model/request
    #: rejection — HTTP 4xx semantics). The failover router cools these down
    #: for much longer than transient 5xx/network failures.
    permanent: bool = False


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
        url = f"{self._base_url}/chat/completions"
        # Bounded retry for transient failures only: HTTP 429 (rate limit,
        # up to RATE_LIMIT_MAX_RETRIES retries) and 5xx (provider-side
        # faults, one retry — they are usually transient gateway/model
        # errors, but a longer loop only adds latency). Each HTTP attempt
        # gets the full request timeout and every sleep is capped
        # (base.RETRY_SLEEP_CAP_SECONDS), so the worst-case added latency
        # stays small against the timeout budget.
        rate_limit_retries = 0
        server_error_retries = 0
        while True:
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        url,
                        headers=self._headers(),
                        json=self._payload(request, stream=False),
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
                    logger.warning(
                        "cloud llm rate limited",
                        extra={"provider": self._provider, "status": status},
                    )
                    raise LLMRateLimitError() from None
                if 400 <= status < 500:
                    # Invalid key, model or request semantics: never transient.
                    logger.warning(
                        "cloud llm rejected request",
                        extra={"provider": self._provider, "status": status},
                    )
                    _reject = CloudProviderError(
                        "The generation provider rejected the request."
                    )
                    _reject.permanent = True
                    raise _reject from None
                logger.warning(
                    "cloud llm server error",
                    extra={"provider": self._provider, "status": status},
                )
                raise CloudProviderError(
                    "The generation provider is currently unavailable."
                ) from None
            except httpx.TimeoutException as exc:
                logger.warning(
                    "cloud llm timed out",
                    extra={"provider": self._provider, "error_type": type(exc).__name__},
                )
                raise LLMTimeoutError() from exc
            except httpx.HTTPError as exc:
                # Network-level failure (connect/read errors and the like):
                # indistinguishable from "provider down" at this layer.
                logger.warning(
                    "cloud llm unreachable",
                    extra={"provider": self._provider, "error_type": type(exc).__name__},
                )
                raise CloudProviderError(
                    "The generation provider is currently unavailable."
                ) from exc
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
        url = f"{self._base_url}/chat/completions"
        # Same bounded retry as generate(), with one streaming-specific
        # constraint: a retry is only possible BEFORE the first token is
        # yielded — once the consumer has received anything, the SSE stream
        # is committed and replaying the request would duplicate output.
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
                    logger.warning(
                        "cloud llm rate limited (stream)",
                        extra={"provider": self._provider, "status": status},
                    )
                    raise LLMRateLimitError() from None
                if 400 <= status < 500:
                    logger.warning(
                        "cloud llm rejected request (stream)",
                        extra={"provider": self._provider, "status": status},
                    )
                    _reject = CloudProviderError(
                        "The generation provider rejected the request."
                    )
                    _reject.permanent = True
                    raise _reject from None
                logger.warning(
                    "cloud llm streaming failed",
                    extra={"provider": self._provider, "status": status},
                )
                raise CloudProviderError(
                    "The generation provider is currently unavailable."
                ) from None
            except httpx.TimeoutException as exc:
                logger.warning(
                    "cloud llm timed out (stream)",
                    extra={"provider": self._provider, "error_type": type(exc).__name__},
                )
                raise LLMTimeoutError() from exc
            except httpx.HTTPError as exc:
                logger.warning(
                    "cloud llm streaming failed",
                    extra={"provider": self._provider, "error_type": type(exc).__name__},
                )
                raise CloudProviderError(
                    "The generation provider is currently unavailable."
                ) from exc

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

    async def probe(self, *, verify_chat: bool = False) -> ProviderHealth:
        """Classified health (brain status contract): distinguishes missing
        configuration, rejected credentials, an unreachable endpoint and a
        model the provider does not offer.

        ``verify_chat=True`` (D-096) follows a passing model-list check with
        one tiny chat completion: a model that is listed and authenticated
        yet cannot answer — a prompt-guard classifier, an embedding model —
        must fail at configuration time, not at the first user question.
        """
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
        if not verify_chat:
            return ProviderHealth(
                state=ProviderHealthState.HEALTHY,
                provider=self._provider,
                model=self._model,
                detail="reachable and model available" if offered is not None else "reachable",
            )
        return await self._verify_chat()

    #: Minimal usability round-trip (D-096). One user turn, capped output.
    _CHAT_VERIFY_PROMPT = "Reply with the single word OK."

    async def _verify_chat(self) -> ProviderHealth:
        """One tiny completion; classifies exactly why a model cannot answer.

        No ``max_tokens`` cap: reasoning models (gpt-oss, QwQ…) spend the
        first tokens of their budget on hidden reasoning, so a small cap
        yields HTTP 200 with an EMPTY ``content`` — a working model wrongly
        classified as degraded (live incident, D-096 follow-up).
        """
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": self._CHAT_VERIFY_PROMPT}],
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.HTTPError:
            return ProviderHealth(
                state=ProviderHealthState.UNAVAILABLE,
                provider=self._provider,
                model=self._model,
                detail="The provider endpoint is unreachable during chat verification "
                "(network error or timeout).",
                chat_verified=False,
            )
        status = response.status_code
        if status == 200:
            try:
                text = str(response.json()["choices"][0]["message"]["content"] or "")
            except (ValueError, KeyError, IndexError, TypeError):
                text = ""
            if text.strip():
                return ProviderHealth(
                    state=ProviderHealthState.HEALTHY,
                    provider=self._provider,
                    model=self._model,
                    detail="chat verified — the model answered a test prompt",
                    chat_verified=True,
                )
            return ProviderHealth(
                state=ProviderHealthState.DEGRADED,
                provider=self._provider,
                model=self._model,
                detail="The model returned an empty response to a test chat request.",
                chat_verified=False,
            )
        if status == 400:
            # The live incident behind D-096: a prompt-guard classifier model
            # passes the /models listing but answers 400 to chat completions.
            return ProviderHealth(
                state=ProviderHealthState.INVALID_CONFIGURATION,
                provider=self._provider,
                model=self._model,
                detail=(
                    f"Model '{self._model}' rejected a chat request (HTTP 400) — it is "
                    "offered but not chat-capable (for example a classifier, guard or "
                    "embedding model). Choose a chat/completions model."
                ),
                chat_verified=False,
            )
        if status in (401, 403):
            return ProviderHealth(
                state=ProviderHealthState.INVALID_CONFIGURATION,
                provider=self._provider,
                model=self._model,
                detail=f"The provider rejected the API key during chat verification "
                f"(HTTP {status}).",
                chat_verified=False,
            )
        if status == 404:
            return ProviderHealth(
                state=ProviderHealthState.INVALID_CONFIGURATION,
                provider=self._provider,
                model=self._model,
                detail=f"Model '{self._model}' was not found for a chat request (HTTP 404).",
                chat_verified=False,
            )
        if status == 429:
            # Rate limited ≠ broken: the model is probably usable; retry.
            return ProviderHealth(
                state=ProviderHealthState.DEGRADED,
                provider=self._provider,
                model=self._model,
                detail="The provider is rate-limiting requests (HTTP 429) — reachable, "
                "but chat could not be verified; retry the test shortly.",
                chat_verified=None,
            )
        return ProviderHealth(
            state=ProviderHealthState.UNAVAILABLE,
            provider=self._provider,
            model=self._model,
            detail=f"The provider returned HTTP {status} during chat verification.",
            chat_verified=False,
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
