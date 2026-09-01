"""LLM provider registry and configuration boundary (REQUIREMENTS.md LLM-003).

Providers are registered by name and resolved from the typed settings object
(``LLM_PROVIDER`` environment variable), so the provider is swappable through
configuration without touching application code.
"""

from collections.abc import Callable

from app.core.config import Settings
from app.core.errors import AppError
from app.llm.base import LLMProvider

ProviderFactory = Callable[[Settings], LLMProvider]


class UnknownProviderError(AppError):
    """The configured LLM provider is not registered."""

    status_code = 500
    code = "UNKNOWN_LLM_PROVIDER"


class ProviderRegistry:
    """Name-to-factory registry of LLM provider implementations."""

    def __init__(self) -> None:
        self._factories: dict[str, ProviderFactory] = {}

    def register(self, name: str, factory: ProviderFactory) -> None:
        self._factories[name] = factory

    def available(self) -> list[str]:
        return sorted(self._factories)

    def create(self, name: str, settings: Settings) -> LLMProvider:
        """Instantiate the provider registered under ``name``.

        Raises:
            UnknownProviderError: if ``name`` has no registered provider.
        """
        try:
            factory = self._factories[name]
        except KeyError:
            registered = ", ".join(self.available()) or "(none)"
            raise UnknownProviderError(
                f"Unknown LLM provider '{name}'. Registered providers: {registered}."
            ) from None
        return factory(settings)


def create_default_registry() -> ProviderRegistry:
    """Build the default registry.

    Ollama is the keyless default generation path (D-033); hosted generation
    providers (D-034/D-080) are registered alongside it, all selected through
    ``LLM_PROVIDER`` configuration.
    """
    from app.llm.gemini import create_gemini_provider
    from app.llm.ollama import create_ollama_provider
    from app.llm.openai_compat import (
        create_grok_provider,
        create_groq_provider,
        create_openai_compatible_provider,
        create_openai_provider,
        create_openrouter_provider,
    )

    registry = ProviderRegistry()
    registry.register("ollama", create_ollama_provider)
    registry.register("openai", create_openai_provider)
    registry.register("gemini", create_gemini_provider)
    registry.register("grok", create_grok_provider)
    registry.register("groq", create_groq_provider)
    registry.register("openrouter", create_openrouter_provider)
    registry.register("openai-compatible", create_openai_compatible_provider)
    return registry
