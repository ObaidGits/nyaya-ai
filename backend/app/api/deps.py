"""FastAPI dependency providers.

Settings, the health-check registry and the LLM provider boundary are resolved
through the application state so they can be injected and replaced in tests
without global mutable singletons inside handlers.
"""

from typing import cast

from fastapi import Request

from app.core.config import Settings
from app.core.errors import LLMProviderNotConfiguredError
from app.core.health import CheckRegistry
from app.llm.base import LLMProvider
from app.llm.registry import ProviderRegistry, UnknownProviderError
from app.providers.runtime import ProviderPoolRuntime


def get_app_settings(request: Request) -> Settings:
    """Return the application settings attached to the running app."""
    return cast(Settings, request.app.state.settings)


def get_check_registry(request: Request) -> CheckRegistry:
    """Return the readiness check registry attached to the running app."""
    return cast(CheckRegistry, request.app.state.check_registry)


def get_provider_registry(request: Request) -> ProviderRegistry:
    """Return the LLM provider registry attached to the running app."""
    return cast(ProviderRegistry, request.app.state.llm_registry)


def get_llm_provider(request: Request) -> LLMProvider:
    """Resolve the LLM provider (REQUIREMENTS.md LLM-002/LLM-003).

    A configured provider pool takes precedence (failover across entries,
    default entry first); with no pool — or an empty one — the unchanged
    single-provider ENV path applies.
    """
    runtime: ProviderPoolRuntime | None = getattr(request.app.state, "provider_pool_runtime", None)
    if runtime is not None and runtime.llm is not None:
        return runtime.llm
    settings = cast(Settings, request.app.state.settings)
    registry = cast(ProviderRegistry, request.app.state.llm_registry)
    try:
        return registry.create(settings.llm_provider, settings)
    except UnknownProviderError as exc:
        raise LLMProviderNotConfiguredError(str(exc)) from exc
