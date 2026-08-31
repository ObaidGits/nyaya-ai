"""Generation test doubles: scripted LLM providers over retrieval fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from app.domain.models import MessageRole
from app.llm.base import (
    ChatMessage,
    GenerationRequest,
    GenerationResult,
    LLMProvider,
    ProviderMetadata,
)
from app.retrieval.models import (
    RetrievalRoute,
    RetrievedEvidence,
    ScoredChunk,
)
from tests.retrieval.fixtures import make_corpus

#: A grounded answer citing retrieved section 103 (and one part citation).
GOOD_ANSWER = (
    "Murder is punishable with death or imprisonment for life [TS s.103]. "
    "For offenders under eighteen the penalty is reduced [TS s.103(1)]."
)

#: Answer with one valid and one fabricated citation.
MIXED_ANSWER = (
    "Murder is punishable with death [TS s.103]. Theft is punishable with imprisonment [TS s.999]."
)

#: Answer with a section claim but no citation at all.
UNCITED_ANSWER = "Section 999 of BNS says theft is punishable with imprisonment."


class ScriptedProvider(LLMProvider):
    """Returns scripted texts in order; records every request it sees."""

    def __init__(self, responses: Sequence[str]) -> None:
        self._responses = list(responses)
        self.requests: list[GenerationRequest] = []

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.requests.append(request)
        text = self._responses.pop(0) if self._responses else ""
        return GenerationResult(text=text, model="scripted")

    def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        async def _gen() -> AsyncIterator[str]:
            yield ""

        return _gen()

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(provider="scripted", model="scripted", supports_streaming=False)

    async def health_check(self) -> bool:
        return True


class FailingProvider(LLMProvider):
    """Always raises — provider failure path (mirrors OllamaProviderError)."""

    def __init__(self) -> None:
        from app.core.errors import AppError

        class _ProviderDown(AppError):
            status_code = 503
            code = "LLM_PROVIDER_UNAVAILABLE"

        self._error_cls = _ProviderDown

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        raise self._error_cls("The generation provider is currently unavailable.")

    def stream(self, request: GenerationRequest) -> AsyncIterator[str]:
        async def _gen() -> AsyncIterator[str]:
            yield ""
            return

        return _gen()

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(provider="failing", model="failing", supports_streaming=False)

    async def health_check(self) -> bool:
        return False


def make_evidence(
    *,
    sufficient: bool = True,
    confidence: float = 1.0,
    chunks: list[ScoredChunk] | None = None,
    query: str = "what is the punishment for murder?",
) -> RetrievedEvidence:
    """Evidence fixture over the synthetic Test Sanhita corpus."""
    if chunks is None:
        corpus = {c.chunk_id: c for c in make_corpus()}
        chunks = [
            ScoredChunk(chunk=corpus["ts-s103-001"], rrf_score=1.0),
            ScoredChunk(chunk=corpus["ts-s103-002"], rrf_score=0.9),
        ]
    return RetrievedEvidence(
        query=query,
        route=RetrievalRoute.STATUTE,
        results=chunks,
        sufficient=sufficient,
        confidence=confidence,
    )


def history(*turns: tuple[str, str]) -> list[ChatMessage]:
    """Build conversation history from (role, content) pairs."""
    return [ChatMessage(role=MessageRole(r), content=c) for r, c in turns]
