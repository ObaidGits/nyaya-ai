"""LLM provider registry and configuration boundary (REQUIREMENTS.md LLM-003).

Providers are registered by name and resolved from the typed settings object
(``LLM_PROVIDER`` environment variable), so the provider is swappable through
configuration without touching application code.

Phase 1 registers no concrete providers: the Ollama and hosted generation
implementations arrive in later phases. Resolving an unregistered provider
raises a clear configuration error rather than falling back silently.
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

    Phase 1 intentionally registers no providers. Later phases register the
    concrete implementations (Ollama first, D-033) here.
    """
    return ProviderRegistry()
