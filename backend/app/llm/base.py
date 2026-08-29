"""LLM provider abstraction (DECISIONS.md D-032, REQUIREMENTS.md LLM-002).

Application code depends only on :class:`LLMProvider`, never on a concrete
provider implementation. Concrete providers (Ollama as the keyless path,
D-033, plus hosted generation providers, D-034) are registered with the
provider registry in later phases and selected through configuration
(``LLM_PROVIDER``), satisfying LLM-003.

This module intentionally contains no generation, citation or retrieval logic.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence

from pydantic import BaseModel

from app.domain.models import MessageRole


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
