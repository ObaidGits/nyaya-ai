"""Streaming chat API tests (REQUIREMENTS D-005/D-006/D-007, A4-*; §32/§37)."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from app.core.config import Settings
from app.main import create_app
from app.retrieval.service import RetrievalService
from app.retrieval.sparse import Bm25SparseIndex
from app.retrieval.store import ChunkStore
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.generation.fixtures import (
    GOOD_ANSWER,
    FailingProvider,
    ScriptedProvider,
    make_evidence,
)
from tests.retrieval.fixtures import FakeDenseRetriever, make_corpus


def _retrieval_service() -> RetrievalService:
    """A real retrieval service over the synthetic corpus (no LLM)."""
    chunks = make_corpus()
    store = ChunkStore(chunks)
    dense = FakeDenseRetriever(
        {
            "What is the punishment for murder?": ["ts-s103-001", "ts-s103-002"],
        }
    )
    return RetrievalService(store, dense, Bm25SparseIndex(chunks), confidence_threshold=0.1)


def _app(provider: ScriptedProvider | FailingProvider) -> FastAPI:
    settings = Settings(_env_file=None)
    app = create_app(settings=settings)
    app.state.llm_registry.register("stub", lambda _s: provider)
    app.state.settings = settings.model_copy(update={"llm_provider": "stub"})
    app.state.retrieval_service = _retrieval_service()
    return app


def _parse_sse(text: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        events.append((event, json.loads(data)))
    return events


@pytest.fixture
def chat_client() -> Iterator[TestClient]:
    app = _app(ScriptedProvider([GOOD_ANSWER]))
    with TestClient(app) as client:
        yield client


def _post(client: TestClient, payload: dict[str, object]) -> str:
    response = client.post("/api/v1/chat", json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return response.text


def test_chat_streams_token_events_progressively(chat_client: TestClient) -> None:
    text = _post(chat_client, {"message": "What is the punishment for murder?"})
    events = _parse_sse(text)
    names = [name for name, _ in events]
    assert names[0] == "token"
    assert names.count("token") > 1  # progressive, not a single wall of text
    assert names[-2] == "sources"
    assert names[-1] == "done"
    streamed = "".join(str(data["text"]) for name, data in events if name == "token")
    assert streamed.strip() == GOOD_ANSWER.strip()


def test_chat_done_event_has_citations_and_confidence(chat_client: TestClient) -> None:
    events = _parse_sse(_post(chat_client, {"message": "What is the punishment for murder?"}))
    done = events[-1][1]
    assert done["refused"] is False
    assert "[TS s.103]" in list(done["citations"])
    assert float(done["confidence"]) > 0.0


def test_chat_sources_event_carries_traceability(chat_client: TestClient) -> None:
    events = _parse_sse(_post(chat_client, {"message": "What is the punishment for murder?"}))
    sources = next(data for name, data in events if name == "sources")["sources"]
    assert sources
    source = sources[0]
    for key in (
        "citation",
        "act",
        "act_short",
        "section_number",
        "section_title",
        "text",
        "page_start",
        "page_end",
        "source_uri",
        "chunk_id",
    ):
        assert key in source
    assert source["citation"] == "[TS s.103]"
    assert source["text"].startswith("Whoever commits murder")


def test_chat_refuses_when_retrieval_insufficient() -> None:
    class _NoEvidenceService:
        def retrieve(
            self,
            query: str,
            flt: object = None,
            *,
            route: object = None,
            session_id: str | None = None,
        ) -> object:
            return make_evidence(sufficient=False, confidence=0.0, chunks=[], query=query)

    app = _app(ScriptedProvider([GOOD_ANSWER]))
    app.state.retrieval_service = _NoEvidenceService()
    with TestClient(app) as client:
        text = _post(client, {"message": "What does the quantum physics statute say?"})
    events = _parse_sse(text)
    tokens = "".join(str(d["text"]) for n, d in events if n == "token")
    assert tokens.strip() == "I don't know based on the available source material."
    done = events[-1][1]
    assert done["refused"] is True


def test_chat_refuses_on_unknown_section_lookup() -> None:
    app = _app(ScriptedProvider([GOOD_ANSWER]))
    with TestClient(app) as client:
        text = _post(client, {"message": "What does section 999 of TS say?"})
    tokens = "".join(str(d["text"]) for n, d in _parse_sse(text) if n == "token")
    assert tokens.strip() == "I don't know based on the available source material."


def test_chat_provider_failure_streams_safe_error() -> None:
    app = _app(FailingProvider())
    with TestClient(app) as client:
        text = _post(client, {"message": "What is the punishment for murder?"})
    events = _parse_sse(text)
    error = next(data for name, data in events if name == "error")
    assert error["code"] == "SERVICE_UNAVAILABLE"
    assert "internal" not in str(error).lower()
    assert "exploded" not in str(error)


def test_chat_multi_turn_history_accepted(chat_client: TestClient) -> None:
    text = _post(
        chat_client,
        {
            "message": "What is the punishment for murder?",
            "history": [
                {"role": "user", "content": "What is murder?"},
                {"role": "assistant", "content": "An offence against life."},
            ],
        },
    )
    assert _parse_sse(text)[-1][0] == "done"


def test_chat_validation_error_returns_structured_error(chat_client: TestClient) -> None:
    response = chat_client.post("/api/v1/chat", json={"message": ""})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["request_id"]


def test_chat_unconfigured_retrieval_returns_503() -> None:
    settings = Settings(_env_file=None)
    app = create_app(settings=settings)
    app.state.llm_registry.register("stub", lambda _s: ScriptedProvider([GOOD_ANSWER]))
    app.state.settings = settings.model_copy(update={"llm_provider": "stub"})
    # Neither statute nor document retrieval configured.
    app.state.retrieval_service = None
    app.state.document_retrieval_service = None
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json={"message": "hello"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "RETRIEVAL_NOT_CONFIGURED"


def test_done_event_includes_document_citations() -> None:
    """`done.citations` reflects document citations, not only statute labels."""
    from app.documents.models import DocumentHit

    doc_id = "caf5697ce6f9489cbe3c468e8af813b8"
    doc_answer = (
        f"The Widget Agreement requires a notice period of 30 days. [Document {doc_id} p.1]"
    )

    class _DocRetrieval:
        def retrieve(self, session_id: str, query: str):  # pragma: no cover - shape
            from app.documents.models import DocumentEvidence

            return DocumentEvidence(
                hits=[
                    DocumentHit(
                        chunk_id=f"{doc_id}-p0001-000",
                        document_id=doc_id,
                        text="The Widget Agreement requires notice of 30 days.",
                        page_start=1,
                        page_end=1,
                        source_uri=f"document:{doc_id}#page=1",
                        score=0.9,
                    )
                ]
            )

    # Rebuild the statute retrieval service WITH document retrieval attached
    # (the combined/document route flows through it, A5-005..A5-012).
    chunks = make_corpus()
    app = _app(ScriptedProvider([doc_answer]))
    app.state.retrieval_service = RetrievalService(
        ChunkStore(chunks),
        FakeDenseRetriever({}),
        Bm25SparseIndex(chunks),
        confidence_threshold=0.1,
        document_retrieval=_DocRetrieval(),
    )
    app.state.document_retrieval_service = _DocRetrieval()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat",
            json={"message": "What notice period does my document require?"},
            headers={"X-Session-Id": "sess-document-0001"},
        )
    assert response.status_code == 200
    done = _parse_sse(response.text)[-1][1]
    assert f"[Document {doc_id}]" in list(done["citations"])
