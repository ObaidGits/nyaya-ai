"""LLM provider abstraction (DECISIONS.md D-032, REQUIREMENTS.md LLM-002).

Application code depends only on :class:`LLMProvider`, never on a concrete
provider implementation. Concrete providers (Ollama as the keyless path,
D-033, plus hosted generation providers, D-034) are registered with the
provider registry in later phases and selected through configuration
(``LLM_PROVIDER``), satisfying LLM-003.

This module intentionally contains no generation, citation or retrieval logic.
"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum

from pydantic import BaseModel

from app.domain.models import MessageRole

# --- Transient provider-failure retry policy (D-034/D-080) ----------------
#
# Cloud generation endpoints — the Groq free tier especially — answer a
# large share of requests with HTTP 429. These constants define the bounded
# retry every cloud provider applies before surfacing an error. Sleeps are
# capped per attempt so a hostile Retry-After cannot stall a request far
# beyond its own timeout budget; worst-case added latency is ~15 s.

#: HTTP 429 (rate limit): retry twice — three attempts total.
RATE_LIMIT_MAX_RETRIES = 2
#: HTTP 5xx (provider-side fault): one cheap retry recovers transient
#: gateway/model-server failures without hiding a broken provider behind
#: a retry storm.
SERVER_ERROR_MAX_RETRIES = 1
#: Hard cap on any single retry sleep (seconds).
RETRY_SLEEP_CAP_SECONDS = 5.0
#: Exponential backoff base when no Retry-After header is present.
_RETRY_BACKOFF_BASE_SECONDS = 0.5


def _parse_retry_after(header: str | None) -> float | None:
    """Parse a Retry-After header (delta-seconds or HTTP-date) to seconds.

    Returns ``None`` when the header is absent or unparseable, so callers
    can fall back to exponential backoff.
    """
    if not header:
        return None
    value = header.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (when - datetime.now(tz=UTC)).total_seconds()


def retry_delay(attempt: int, retry_after_header: str | None = None) -> float:
    """Seconds to sleep before retry attempt ``attempt`` (1-based).

    A parseable ``Retry-After`` wins; otherwise exponential backoff
    (0.5 s, 1 s, 2 s, ...). Both are capped at
    :data:`RETRY_SLEEP_CAP_SECONDS`; a past HTTP-date clamps to zero.
    """
    if retry_after_header:
        parsed = _parse_retry_after(retry_after_header)
        if parsed is not None:
            return max(0.0, min(parsed, RETRY_SLEEP_CAP_SECONDS))
    return min(RETRY_SLEEP_CAP_SECONDS, _RETRY_BACKOFF_BASE_SECONDS * 2.0 ** (attempt - 1))


async def retry_sleep(seconds: float) -> None:
    """Retry-loop sleep. Indirection seam so tests can observe delays."""
    await asyncio.sleep(seconds)


class ProviderHealthState(StrEnum):
    """Authoritative provider usability states (brain status contract).

    ``healthy`` means the provider is configured, authenticated AND the
    configured model is available — the only state in which the UI may show
    "Brain active".
    """

    NOT_CONFIGURED = "not_configured"
    INVALID_CONFIGURATION = "invalid_configuration"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"
    HEALTHY = "healthy"
    ERROR = "error"


class ProviderHealth(BaseModel):
    """Rich health probe result; never contains secrets."""

    state: ProviderHealthState
    provider: str
    model: str | None = None
    detail: str = ""
    #: Chat-capability verification (D-096): ``True``/``False`` after an
    #: explicit generation round-trip; ``None`` = not tested by this probe
    #: (the cheap polled probe). A model can be listed and authenticated yet
    #: unable to answer (e.g. a prompt-guard classifier) — only a real
    #: completion proves usability.
    chat_verified: bool | None = None


class ChatMessage(BaseModel):
    """A single message in a generation request."""

    role: MessageRole
    content: str


class GenerationRequest(BaseModel):
    """Input to a generation call."""

    messages: Sequence[ChatMessage]


class GenerationResult(BaseModel):
    """Output of a completed generation call."""

    text: str
    model: str | None = None
    # Token usage when the provider reports it (F-030); None means unknown.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ProviderMetadata(BaseModel):
    """Descriptive metadata for the configured provider."""

    provider: str
    model: str
    supports_streaming: bool = True


class LLMProvider(ABC):
    """Abstract boundary between the application and LLM backends.

    Subclasses implement ``generate``/``stream``/``metadata`` per DECISIONS.md
    D-032; ``health_check`` supports the readiness endpoint's model check
    (REQUIREMENTS.md D-031).
    """

    @abstractmethod
    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate a complete response."""

    @abstractmethod
    def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        """Stream response text incrementally."""

    @abstractmethod
    def metadata(self) -> ProviderMetadata:
        """Describe the configured provider and model."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Return ``True`` when the provider is reachable and usable."""

    async def probe(self, *, verify_chat: bool = False) -> ProviderHealth:
        """Classified health for the brain status contract.

        The default wraps :meth:`health_check` (bool) so simple providers and
        test doubles keep working; concrete providers override it with a
        state-classifying probe (auth rejected vs unreachable vs model
        missing) so the UI never guesses.

        ``verify_chat=True`` additionally performs one tiny generation
        round-trip so a model that is listed but cannot answer (D-096) is
        caught at configuration time instead of at the first user question.
        """
        healthy = await self.health_check()
        meta = self.metadata()
        return ProviderHealth(
            state=ProviderHealthState.HEALTHY if healthy else ProviderHealthState.UNAVAILABLE,
            provider=meta.provider,
            model=meta.model,
            detail="" if healthy else "provider unreachable or not configured",
        )
