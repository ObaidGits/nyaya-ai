"""Streaming chat endpoint (REQUIREMENTS D-005/D-006/D-007, A4-*; ARCHITECTURE §32, §37).

``POST /api/v1/chat`` — lifecycle per §32.1::

    request ─▶ conversation short-circuit (D-067, greetings only)
            ─▶ retrieval (intent → lookup / hybrid) ─▶ confidence gate
            ─▶ refusal | grounded generation ─▶ citation guard ─▶ SSE stream

The response is Server-Sent Events (§37): answer tokens stream progressively
(``event: token``), followed by ``event: sources`` (source drawer payload,
§19) and ``event: done``. Errors mid-stream are emitted as
``event: error`` with the standard error envelope — no stack traces or
provider internals leave the server.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.api.deps import get_llm_provider
from app.api.v1.documents import _SAFE_SESSION_RE, SessionMissingError
from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limit import CHAT_SCOPE, enforce_rate_limit
from app.core.request_id import get_request_id
from app.domain.models import MessageRole
from app.generation.citation_guard import Citation
from app.generation.conversation import conversational_category, reply_for_category
from app.generation.service import GenerationOutcome, GenerationService
from app.language.conversation import multilingual_category, multilingual_reply
from app.language.models import LANGUAGE_PREFERENCES, LanguageCode
from app.language.service import LanguageService
from app.llm.base import ChatMessage, LLMProvider
from app.observability.metrics import ESTIMATED_COST, LAST_QUERY_COST, TOKENS
from app.retrieval.models import RetrievedEvidence
from app.retrieval.service import RetrievalService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class _NullEmbedder:
    """Zero-vector embedder for the empty fallback statute index."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    def dimensions(self) -> int:
        return 1


class ChatTurn(BaseModel):
    """One prior conversation turn (multi-turn context, D-007).

    History is untrusted client input: only user/assistant turns are
    accepted. A ``system`` role would inject instructions after the real
    system prompt, so it is rejected at the API boundary.
    """

    role: MessageRole
    content: str = Field(min_length=0, max_length=8000)

    @field_validator("role")
    @classmethod
    def _reject_system_role(cls, value: MessageRole) -> MessageRole:
        if value == MessageRole.SYSTEM:
            raise ValueError("system role is not allowed in chat history")
        return value


#: Hard ceiling on accepted turns, independent of the (admin-tunable, <= 50)
#: chat_history_max_turns slice used for generation: the request itself must
#: stay bounded so a single body cannot carry unbounded untrusted text.
HISTORY_MAX_TURNS = 40


class ChatRequest(BaseModel):
    """Chat request body (D-005; language preference D-077).

    ``language`` selects the answer language: "auto" (detect from the
    message) or a supported language code. It defaults to "auto", which
    resolves to English for every Latin-script message — the existing
    English workflow is byte-identical when the field is absent.
    """

    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=HISTORY_MAX_TURNS)
    language: str = Field(default="auto")

    @field_validator("language")
    @classmethod
    def _validate_language(cls, value: str) -> str:
        if value not in LANGUAGE_PREFERENCES:
            raise ValueError("language must be 'auto' or a supported language code")
        return value


class ChatResponseMeta(BaseModel):
    """Final event metadata: confidence and citation summary.

    ``confidence`` is the retrieval confidence (ARCHITECTURE §15); it is
    None on conversational turns where no retrieval ran (D-067).
    """

    confidence: float | None = None
    refused: bool
    model: str | None = None
    citations: list[str] = Field(default_factory=list)
    # Answer language actually used (D-077/D-079): the TTS client reads this
    # so spoken output always follows the answer language, never the UI
    # preference guess.
    language: str = "en"
    # Code-authored refusal reason sentence, already part of the streamed
    # refusal text; None on grounded answers and plain refusals.
    refusal_reason: str | None = None


def _sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _citation_labels(citations: list[Citation], document_ids: list[str]) -> list[str]:
    """Statute labels plus cited document ids, deduplicated, in answer order.

    The ``done`` event's citation list must reflect the ACTUAL answer
    citations — statute labels and user-document ids alike — otherwise the
    API contract understates what the answer relies on when the answer
    cites an uploaded document but no statute section.
    """
    seen: list[str] = []
    for citation in citations:
        if citation.label not in seen:
            seen.append(citation.label)
    for document_id in document_ids:
        label = f"[Document {document_id}]"
        if label not in seen:
            seen.append(label)
    return seen


async def _run_chat(
    request: ChatRequest,
    provider: LLMProvider,
    retrieval: RetrievalService,
    session_id: str | None,
    settings: Settings | None = None,
    language_service: LanguageService | None = None,
) -> AsyncIterator[str]:
    """Execute the chat lifecycle and stream SSE events (§32.1, §37).

    Language handling (D-077) wraps the existing pipeline without altering
    it: conversational short-circuit first (fixed reply in the request
    language, no retrieval, no translation), then — for non-English input —
    an English translation of the query used ONLY for route/intent detection
    and retrieval. Generation keeps the user's original question; the
    answer language is a code-controlled prompt instruction. Citations,
    evidence, refusal, and the confidence gate are untouched.
    """
    languages = language_service or LanguageService()
    try:
        answer_language = languages.resolve(request.language, request.message)

        # Deterministic conversational short-circuit (D-067/D-068/D-077):
        # an exact-match social formula or whole-message identity question —
        # in English or any supported Indic language — gets a fixed,
        # capability-free reply. No retrieval, no translation call, no LLM
        # call, no citations: the reply makes no legal claim.
        category = conversational_category(request.message) or multilingual_category(
            request.message
        )
        if category is not None:
            reply = (
                multilingual_reply(category, answer_language)
                if answer_language != LanguageCode.EN
                else None
            )
            if reply is None:
                # English replies: message-aware variant pick (greeting
                # variety) — deterministic per message, never random.
                reply = reply_for_category(category, request.message)
            logger.info(
                "conversational short-circuit",
                extra={"event": "conversation_short_circuit", "language": answer_language.value},
            )
            for token in _tokenize(reply or ""):
                yield _sse("token", {"text": token})
            yield _sse("sources", {"sources": []})
            yield _sse(
                "done",
                ChatResponseMeta(
                    confidence=None,
                    refused=False,
                    model=None,
                    citations=[],
                    language=answer_language.value,
                ).model_dump(),
            )
            return

        # Non-English input: translate the query to English for routing and
        # retrieval only. The original question stays in the generation
        # prompt; the translation is never legal evidence and is never
        # shown to the user. Failure falls back to the original message,
        # which retrieves nothing and refuses — conservative.
        retrieval_query = request.message
        detected = languages.detect(request.message)
        if detected != LanguageCode.EN:
            translated = await languages.translate_query(provider, request.message, detected)
            if translated is not None:
                retrieval_query = translated
                logger.info(
                    "query translated for retrieval",
                    extra={
                        "event": "language_query_translated",
                        "language": detected.value,
                        "translation_length": len(translated),
                    },
                )

        history = [
            ChatMessage(role=turn.role, content=turn.content)
            for turn in request.history[
                -(settings.chat_history_max_turns if settings is not None else 20) :
            ]
        ]
        evidence: RetrievedEvidence = await _retrieve(
            retrieval,
            retrieval_query,
            session_id,
            document_context=_document_context_from_history(request.history),
        )
        service = GenerationService(provider)
        outcome: GenerationOutcome = await service.answer(
            request.message,
            evidence,
            history,
            answer_language=answer_language,
        )

        _record_usage(outcome, settings)

        if outcome.refused:
            # Refusal text is code-controlled and already in the answer
            # language (D-077): grounded refusal, never model-generated.
            yield _sse("token", {"text": outcome.answer})
        else:
            # Progressively stream the validated answer in token-sized pieces
            # (§37: tokens must render progressively, not a wall of text).
            for token in _tokenize(outcome.answer):
                yield _sse("token", {"text": token})

        yield _sse(
            "sources",
            {"sources": outcome.sources},
        )
        yield _sse(
            "done",
            ChatResponseMeta(
                confidence=evidence.confidence,
                refused=outcome.refused,
                model=outcome.model,
                citations=_citation_labels(
                    outcome.citations.valid_citations,
                    outcome.citations.cited_document_ids,
                ),
                language=answer_language.value,
                refusal_reason=outcome.refusal_reason,
            ).model_dump(),
        )
    except AppError as exc:
        # Typed application errors keep their truthful code and message —
        # LLM_RATE_LIMITED, LLM_TIMEOUT, LLM_PROVIDER_UNAVAILABLE,
        # LLM_EMPTY_RESPONSE, RETRIEVAL_NOT_CONFIGURED — so clients can
        # distinguish "retry shortly" from "broken". AppError messages are
        # code-authored constants; provider bodies, prompts and secrets
        # never reach this layer.
        logger.warning(
            "chat stream failed",
            extra={"error_code": exc.code, "error_message": exc.message},
        )
        yield _sse(
            "error",
            {
                "code": exc.code,
                "message": exc.message,
                "request_id": get_request_id(),
            },
        )
    except Exception:
        # Unknown failure: the full traceback stays in the server log; the
        # client gets a generic event — no internals, no exception text.
        logger.exception("chat stream failed")
        yield _sse(
            "error",
            {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred. Please try again later.",
                "request_id": get_request_id(),
            },
        )


def _record_usage(outcome: GenerationOutcome, settings: Settings | None) -> None:
    """Record token usage and estimated query cost (F-030, F-034..F-036)."""
    if outcome.prompt_tokens is not None:
        TOKENS.inc(outcome.prompt_tokens, kind="input")
    if outcome.completion_tokens is not None:
        TOKENS.inc(outcome.completion_tokens, kind="output")
    if outcome.prompt_tokens or outcome.completion_tokens:
        rate_in = settings.llm_cost_per_1k_input_tokens if settings else 0.0
        rate_out = settings.llm_cost_per_1k_output_tokens if settings else 0.0
        cost = (outcome.prompt_tokens or 0) / 1000 * rate_in + (
            outcome.completion_tokens or 0
        ) / 1000 * rate_out
        ESTIMATED_COST.inc(cost)
        LAST_QUERY_COST.set(cost)


async def _retrieve(
    retrieval: RetrievalService,
    message: str,
    session_id: str | None,
    document_context: list[str] | None = None,
) -> RetrievedEvidence:
    """Retrieve evidence in a thread-friendly seam (sync service under async).

    ``session_id`` scopes document retrieval (§21); statute questions are
    unaffected. Without a session id document routes fail closed.
    ``document_context`` carries the documents cited in recent assistant
    turns so follow-up references ("that document", "the other document")
    resolve deterministically — the citation labels are code-controlled,
    so parsing them is structured state, not model-text guessing.
    """
    import asyncio

    return await asyncio.to_thread(
        retrieval.retrieve, message, session_id=session_id, document_context=document_context
    )


_DOCUMENT_CITATION_RE = re.compile(r"\[Document ([0-9a-f]{8,})")


def _document_context_from_history(history: list[ChatTurn]) -> list[str] | None:
    """Document ids cited by the most recent assistant turn, if any."""
    for turn in reversed(history):
        if turn.role == MessageRole.ASSISTANT:
            ids = _DOCUMENT_CITATION_RE.findall(turn.content)
            if ids:
                return list(dict.fromkeys(ids))[:4]
            return None
    return None


def _tokenize(text: str) -> list[str]:
    """Split validated answer into progressive render tokens."""
    if not text:
        return []
    # Keep the space with each word so the client can join tokens verbatim;
    # only the last word goes without a trailing space.
    pieces = text.split(" ")
    return [piece + " " for piece in pieces[:-1]] + ([pieces[-1]] if pieces else [])


def get_retrieval_service(request: Request) -> RetrievalService:
    """Resolve the retrieval service from application state (injectable).

    When no statute corpus is configured but session document retrieval is,
    chat still works: the statute side is an empty store, so statute
    questions refuse honestly while document questions retrieve normally.
    """
    service: RetrievalService | None = getattr(request.app.state, "retrieval_service", None)
    if service is not None:
        return service

    document_retrieval = getattr(request.app.state, "document_retrieval_service", None)
    if document_retrieval is not None:
        from app.retrieval.dense import CosineDenseIndex
        from app.retrieval.sparse import Bm25SparseIndex
        from app.retrieval.store import ChunkStore

        settings = getattr(request.app.state, "settings", None)
        threshold = (
            settings.document_retrieval_confidence_threshold if settings is not None else 0.05
        )
        return RetrievalService(
            ChunkStore([]),
            CosineDenseIndex([], _NullEmbedder()),
            Bm25SparseIndex([]),
            document_confidence_threshold=threshold,
            document_retrieval=document_retrieval,
        )

    from app.core.errors import ServiceUnavailableError

    raise ServiceUnavailableError(
        "Retrieval is not configured on this instance.",
        code="RETRIEVAL_NOT_CONFIGURED",
    )


@router.post("", response_class=StreamingResponse)
async def chat(
    request: ChatRequest,
    provider: Annotated[LLMProvider, Depends(get_llm_provider)],
    retrieval: Annotated[RetrievalService, Depends(get_retrieval_service)],
    raw_request: Request,
    x_session_id: Annotated[str | None, Header(alias="X-Session-Id")] = None,
) -> StreamingResponse:
    """Stream a grounded, citation-validated answer (D-005/D-006, D-049)."""
    # A missing session id is allowed (anonymous, statute-only turns), but a
    # malformed one is rejected outright: ownership must never resolve to an
    # attacker-chosen identity that the document endpoints would refuse.
    if x_session_id is not None and not _SAFE_SESSION_RE.fullmatch(x_session_id):
        raise SessionMissingError(
            "The X-Session-Id header must match ^[A-Za-z0-9_-]{8,128}$.",
            code="SESSION_REQUIRED",
        )
    settings = getattr(raw_request.app.state, "settings", None)
    limiter = getattr(raw_request.app.state, "rate_limiter", None)
    if limiter is not None and settings is not None:
        # H5: the PRIMARY budget is keyed by CLIENT IP, not the client-
        # controlled X-Session-Id — sessions are anonymous and freely
        # rotatable, so a session key is trivially bypassed. With uvicorn
        # --proxy-headers (Dockerfile) this is the real user IP behind nginx.
        client_host = raw_request.client.host if raw_request.client else "anonymous"
        enforce_rate_limit(
            limiter,
            scope=CHAT_SCOPE,
            key=client_host,
            limit=settings.rate_limit_chat_per_minute,
            window_seconds=60.0,
        )
        # Secondary per-session budget: a NAT-shared IP (office/college) gets
        # the per-IP budget, while a single session still cannot hammer the
        # LLM even when many IPs sit behind one session id.
        if x_session_id is not None:
            enforce_rate_limit(
                limiter,
                scope=f"{CHAT_SCOPE}_session",
                key=x_session_id,
                limit=settings.rate_limit_chat_per_minute,
                window_seconds=60.0,
            )
    return StreamingResponse(
        _run_chat(
            request,
            provider,
            retrieval,
            x_session_id,
            settings,
            language_service=getattr(raw_request.app.state, "language_service", None),
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
