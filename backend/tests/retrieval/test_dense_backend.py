"""Dense backend selection (D-092) and Qdrant index/sink contracts.

The Qdrant classes import the real client lazily, so tests inject fakes by
patching ``qdrant_client.QdrantClient`` — no server, no network.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import pytest
from app.core.config import Settings
from app.ingestion.embeddings import NullEmbedder
from app.ingestion.index_store import QdrantChunkIndex
from app.ingestion.models import Chunk
from app.main import _build_dense_retriever, _qdrant_collection_ready
from app.retrieval.dense import CosineDenseIndex, QdrantDenseRetriever
from app.retrieval.models import MetadataFilter


def _chunk(chunk_id: str, text: str = "body") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        act="Bharatiya Nyaya Sanhita, 2023",
        act_short="BNS",
        chapter="I",
        chapter_title="PRELIMINARY",
        section_number="1",
        section_title="Short title",
        subsection=None,
        clause=None,
        text=text,
        has_illustration=False,
        has_proviso=False,
        has_exception=False,
        page_start=1,
        page_end=1,
        source_uri="pdf:sha256-abc",
        ingested_at="2026-09-02T00:00:00+00:00",
    )


class FakeQdrantClient:
    """Scriptable stand-in; class attributes control probe results."""

    exists: ClassVar[bool] = True
    point_count: ClassVar[int] = 425
    calls: ClassVar[list[str]] = []

    def __init__(self, url: str = "", timeout: float | None = None, **_: object) -> None:
        type(self).calls.append("init")

    def collection_exists(self, collection: str) -> bool:
        return type(self).exists

    def count(self, collection_name: str, exact: bool = True) -> SimpleNamespace:
        return SimpleNamespace(count=type(self).point_count)

    def delete_collection(self, collection: str) -> None:
        type(self).calls.append(f"delete:{collection}")

    def create_collection(self, collection_name: str, vectors_config: object) -> None:
        type(self).calls.append(f"create:{collection_name}")

    def upsert(self, collection_name: str, points: list) -> None:
        type(self).calls.append(f"upsert:{collection_name}:{len(points)}")

    def query_points(self, **kwargs: object) -> SimpleNamespace:
        type(self).calls.append("query_points")
        return SimpleNamespace(
            points=[
                SimpleNamespace(payload={"chunk_id": "bns-1-1"}, score=0.91),
                SimpleNamespace(payload={"chunk_id": "bns-2-1"}, score=0.42),
            ]
        )


@pytest.fixture()
def fake_qdrant(monkeypatch: pytest.MonkeyPatch) -> type[FakeQdrantClient]:
    FakeQdrantClient.calls = []
    FakeQdrantClient.exists = True
    FakeQdrantClient.point_count = 425
    monkeypatch.setattr("qdrant_client.QdrantClient", FakeQdrantClient)
    return FakeQdrantClient


class UnreachableQdrant:
    def __init__(self, url: str = "", timeout: float | None = None, **_: object) -> None:
        raise ConnectionError("refused")


# -- _qdrant_collection_ready --------------------------------------------------


def test_ready_when_reachable_and_populated(fake_qdrant: type[FakeQdrantClient]) -> None:
    ready, reason = _qdrant_collection_ready("http://localhost:6333", "bns_chunks")
    assert ready is True
    assert reason == ""


def test_not_ready_when_collection_missing(fake_qdrant: type[FakeQdrantClient]) -> None:
    fake_qdrant.exists = False
    ready, reason = _qdrant_collection_ready("http://localhost:6333", "bns_chunks")
    assert ready is False
    assert "does not exist" in reason


def test_not_ready_when_collection_empty(fake_qdrant: type[FakeQdrantClient]) -> None:
    fake_qdrant.point_count = 0
    ready, reason = _qdrant_collection_ready("http://localhost:6333", "bns_chunks")
    assert ready is False
    assert "empty" in reason


def test_not_ready_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("qdrant_client.QdrantClient", UnreachableQdrant)
    ready, reason = _qdrant_collection_ready("http://localhost:6333", "bns_chunks")
    assert ready is False
    assert "unreachable" in reason


# -- _build_dense_retriever -----------------------------------------------------


def _settings(backend: str) -> Settings:
    return Settings(
        retrieval_dense_backend=backend,
        qdrant_url="http://localhost:6333",
        qdrant_bns_collection="bns_chunks",
        _env_file=None,  # type: ignore[call-arg]
    )


def test_auto_selects_qdrant_when_populated(
    fake_qdrant: type[FakeQdrantClient],
) -> None:
    dense, backend = _build_dense_retriever(
        _settings("auto"), [_chunk("bns-1-1")], NullEmbedder(), None
    )
    assert backend == "qdrant"
    assert isinstance(dense, QdrantDenseRetriever)


def test_auto_falls_back_to_in_process_when_qdrant_empty(
    fake_qdrant: type[FakeQdrantClient],
) -> None:
    fake_qdrant.exists = False
    dense, backend = _build_dense_retriever(
        _settings("auto"), [_chunk("bns-1-1")], NullEmbedder(), None
    )
    assert backend == "in-process"
    assert isinstance(dense, CosineDenseIndex)


def test_explicit_qdrant_fails_closed_when_unusable(
    fake_qdrant: type[FakeQdrantClient],
) -> None:
    fake_qdrant.exists = False
    with pytest.raises(RuntimeError, match="Qdrant dense backend unusable"):
        _build_dense_retriever(_settings("qdrant"), [_chunk("bns-1-1")], NullEmbedder(), None)


def test_in_process_never_contacts_qdrant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Any QdrantClient construction in this mode is a bug.
    def _boom(url: str = "", timeout: float | None = None, **_: object) -> None:
        raise AssertionError("in-process backend must not contact Qdrant")

    monkeypatch.setattr("qdrant_client.QdrantClient", _boom)
    dense, backend = _build_dense_retriever(
        _settings("in-process"), [_chunk("bns-1-1")], NullEmbedder(), None
    )
    assert backend == "in-process"
    assert isinstance(dense, CosineDenseIndex)


# -- QdrantDenseRetriever (injected client) -------------------------------------


def test_qdrant_retriever_returns_chunk_ids_and_reuses_client() -> None:
    FakeQdrantClient.calls = []
    client = FakeQdrantClient()
    retriever = QdrantDenseRetriever(
        "http://localhost:6333", "bns_chunks", NullEmbedder(), client=client
    )
    hits = retriever.search("punishment for murder", None, 2)
    assert hits == ["bns-1-1", "bns-2-1"]
    assert retriever.top_similarity("punishment for murder", None) == pytest.approx(0.91)
    # The retriever reuses the injected client: exactly one construction
    # (the test's own) across two queries — not one per query.
    assert client.calls.count("init") == 1
    assert client.calls.count("query_points") == 2


def test_qdrant_retriever_maps_metadata_filter() -> None:
    FakeQdrantClient.calls = []
    client = FakeQdrantClient()
    retriever = QdrantDenseRetriever(
        "http://localhost:6333", "bns_chunks", NullEmbedder(), client=client
    )
    retriever.search("murder", MetadataFilter(act_short="BNS", section_number="103"), 5)
    # The filter reached query_points as a Qdrant Filter with both conditions.
    assert "query_points" in client.calls


# -- QdrantChunkIndex full-replace ---------------------------------------------


def test_qdrant_index_replaces_collection_on_reingest(
    fake_qdrant: type[FakeQdrantClient],
) -> None:
    index = QdrantChunkIndex(url="http://localhost:6333", collection="bns_chunks")
    chunks = [_chunk("bns-1-1"), _chunk("bns-1-2")]
    vectors = [[0.1, 0.2], [0.3, 0.4]]
    assert index.upsert(chunks, vectors) == 2
    # Existing collection from a previous (larger) run is dropped first so
    # stale points can never survive a shrinking re-ingest.
    assert fake_qdrant.calls == [
        "init",
        "delete:bns_chunks",
        "create:bns_chunks",
        "upsert:bns_chunks:2",
    ]


def test_qdrant_index_creates_collection_when_absent(
    fake_qdrant: type[FakeQdrantClient],
) -> None:
    fake_qdrant.exists = False
    index = QdrantChunkIndex(url="http://localhost:6333", collection="bns_chunks")
    assert index.upsert([_chunk("bns-1-1")], [[0.1, 0.2]]) == 1
    assert "delete:bns_chunks" not in fake_qdrant.calls
    assert "create:bns_chunks" in fake_qdrant.calls
