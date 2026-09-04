"""LLM provider pool runtime (D-1XX provider failover, 2026-09).

Builds a ``FailoverLLMProvider`` from a persisted pool config: each entry
becomes a pre-instantiated provider (cheap objects — network clients are
created per call), and the shared ``FailoverRouter`` does the ordering,
cooldown and bounded failover. An empty pool yields ``None`` so callers
fall back to the unchanged single-provider ENV path.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from pydantic import SecretStr

from app.core.config import Settings
from app.llm.base import (
    GenerationRequest,
    GenerationResult,
    LLMProvider,
    ProviderHealth,
    ProviderHealthState,
    ProviderMetadata,
)
from app.llm.gemini import DEFAULT_BASE_URL as GEMINI_DEFAULT_BASE_URL
from app.llm.openai_compat import PROFILES
from app.llm.registry import ProviderRegistry
from app.providers.health import HealthBoard
from app.providers.models import (
    PoolSecrets,
    ProviderEntryConfig,
    ProviderPoolConfig,
)
from app.providers.router import FailoverPolicy, FailoverRouter

logger = logging.getLogger(__name__)

#: Keyless default endpoints (matches the factories' own defaults).
_OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"


def entry_settings(base: Settings, entry: ProviderEntryConfig, api_key: str) -> Settings:
    """Effective Settings for one pool entry.

    Same semantics as the admin console draft: an entry-specified base_url
    wins; otherwise the base setting's URL when the provider matches (a
    saved custom endpoint is intentional), else the provider's default.
    """
    if entry.base_url:
        base_url = entry.base_url
    elif entry.provider == base.llm_provider:
        base_url = base.llm_base_url
    else:
        base_url = provider_default_base_url(entry.provider)
    updates: dict[str, Any] = {
        "llm_provider": entry.provider,
        "llm_base_url": base_url,
    }
    if entry.model:
        updates["llm_model"] = entry.model
    if api_key:
        updates["llm_api_key"] = SecretStr(api_key)
    return base.model_copy(update=updates)


def provider_default_base_url(provider: str) -> str:
    if provider == "gemini":
        return GEMINI_DEFAULT_BASE_URL
    if provider == "ollama":
        return _OLLAMA_DEFAULT_BASE_URL
    return PROFILES.get(provider, ("", "", ""))[0]


class FailoverLLMProvider(LLMProvider):
    """A single LLMProvider facade over the whole pool.

    The rest of the application keeps talking to "the" LLM provider
    (generate/stream/metadata); failover is invisible to callers except
    through ``last_entry_id`` (observability) and the health snapshot.
    """

    def __init__(
        self,
        config: ProviderPoolConfig,
        providers: dict[str, LLMProvider],
        board: HealthBoard,
        policy: FailoverPolicy | None = None,
    ) -> None:
        self._config = config
        self._providers = providers
        self._router = FailoverRouter("llm", config, self._resolve, board, policy)
        self._board = board
        #: Entry that served the most recent request ("unknown" before
        #: the first request) — surfaced to admin/metrics, never guessed.
        self.last_entry_id: str | None = None

    def _resolve(self, entry: ProviderEntryConfig) -> LLMProvider:
        return self._providers[entry.id]

    # -- LLMProvider contract ---------------------------------------

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        result, entry_id = await self._router.run(lambda provider: provider.generate(request))
        self.last_entry_id = entry_id
        return result

    def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        return self._stream(request)

    async def _stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        # FailoverRouter.stream does not report which entry won; after
        # consumption the health board's success recording is the
        # authoritative signal for last_entry_id (observability only).
        router_stream = self._router.stream(lambda provider: provider.stream(request))
        try:
            async for chunk in router_stream:
                yield chunk
        finally:
            self.last_entry_id = self._latest_healthy_entry()

    def _latest_healthy_entry(self) -> str | None:
        ordered = self._config.ordered_entries()
        for entry in ordered:
            state = self._board.snapshot("llm").get(f"llm:{entry.id}")
            if state and state.state == "healthy":
                return entry.id
        return ordered[0].id if ordered else None

    def metadata(self) -> ProviderMetadata:
        ordered = self._config.ordered_entries()
        if not ordered:
            return ProviderMetadata(provider="pool", model="(empty pool)", supports_streaming=True)
        primary = self._providers.get(ordered[0].id)
        if primary is None:
            return ProviderMetadata(
                provider="pool", model="(unresolvable)", supports_streaming=True
            )
        meta = primary.metadata()
        if len(ordered) > 1:
            return ProviderMetadata(
                provider=meta.provider,
                model=f"{meta.model} (+{len(ordered) - 1} failover)",
                supports_streaming=meta.supports_streaming,
            )
        return meta

    async def health_check(self) -> bool:
        ordered = self._config.ordered_entries()
        for entry in ordered:
            provider = self._providers.get(entry.id)
            if provider is not None and await provider.health_check():
                return True
        return False

    async def probe(self, *, verify_chat: bool = False) -> ProviderHealth:
        states: list[ProviderHealth] = []
        for entry in self._config.ordered_entries():
            provider = self._providers.get(entry.id)
            if provider is None:
                continue
            states.append(await provider.probe(verify_chat=verify_chat))
        if not states:
            return ProviderHealth(
                state=ProviderHealthState.NOT_CONFIGURED,
                provider="pool",
                model="(empty pool)",
                detail="no enabled entries",
            )
        healthy = next((s for s in states if s.state == ProviderHealthState.HEALTHY), None)
        if healthy is not None:
            return healthy
        detail = "; ".join(f"{s.provider}/{s.model}: {s.detail or s.state.value}" for s in states)
        return ProviderHealth(
            state=states[0].state,
            provider=states[0].provider,
            model=states[0].model,
            detail=f"all pool entries unhealthy — {detail}",
        )


def build_llm_failover_provider(
    config: ProviderPoolConfig,
    secrets: PoolSecrets,
    settings: Settings,
    registry: ProviderRegistry,
    board: HealthBoard,
    policy: FailoverPolicy | None = None,
) -> FailoverLLMProvider | None:
    """Instantiate the pool, or ``None`` when nothing is enabled.

    Unbuildable entries (unknown provider name) are logged and skipped
    rather than poisoning the whole pool — one bad entry must never take
    down the working ones.
    """
    providers: dict[str, LLMProvider] = {}
    for entry in config.enabled_entries():
        try:
            entry_settings_obj = entry_settings(settings, entry, secrets.get("llm", entry.id))
            providers[entry.id] = registry.create(entry.provider, entry_settings_obj)
        except Exception:
            logger.exception(
                "llm pool entry unbuildable, skipping",
                extra={"entry": entry.id, "provider": entry.provider},
            )
    if not providers:
        return None
    return FailoverLLMProvider(config, providers, board, policy)
