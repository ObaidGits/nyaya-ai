"""Chunk store + integration against the indexed Phase 2 dev corpus.

The dev corpus (``data/processed/bnss-dev_chunks.jsonl``, produced by
``scripts/ingest.py --spec bnss-dev``) is a temporary development fixture:
the file is BNSS, not the required BNS source. These tests only verify
the retrieval stack works over the real indexed artifact; final BNS
retrieval quality stays BLOCKED pending the correct source PDF.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.retrieval.dense import CosineDenseIndex
from app.retrieval.service import RetrievalService
from app.retrieval.sparse import Bm25SparseIndex
from app.retrieval.store import ChunkStore
from tests.retrieval.fixtures import DeterministicEmbedder

DEV_JSONL = Path(__file__).resolve().parents[3] / "data" / "processed" / "bnss-dev_chunks.jsonl"

pytestmark = pytest.mark.skipif(
    not DEV_JSONL.exists(), reason="dev corpus artifact not built (run scripts/ingest.py)"
)


@pytest.fixture(scope="module")
def store() -> ChunkStore:
    return ChunkStore.from_jsonl(DEV_JSONL)


def test_dev_corpus_loads(store: ChunkStore) -> None:
    assert len(store.chunks) > 500
    assert all(c.chunk_id.startswith("bnss-s") for c in store.chunks)


def test_deterministic_section_lookup_on_dev_corpus(store: ChunkStore) -> None:
    chunks = store.section_lookup("103", act_short="BNSS")
    assert chunks
    assert all(c.section_number == "103" for c in chunks)
    assert all(c.act_short == "BNSS" for c in chunks)


def test_hybrid_retrieval_over_dev_corpus(store: ChunkStore) -> None:
    service = RetrievalService(
        store=store,
        dense=CosineDenseIndex(store.chunks, DeterministicEmbedder()),
        sparse=Bm25SparseIndex(store.chunks),
    )
    evidence = service.retrieve("bail provisions")
    assert evidence.results
    top = evidence.results[0]
    assert top.chunk.act_short == "BNSS"
    assert top.chunk.text


def test_section_intent_lookup_over_dev_corpus(store: ChunkStore) -> None:
    service = RetrievalService(
        store=store,
        dense=CosineDenseIndex(store.chunks, DeterministicEmbedder()),
        sparse=Bm25SparseIndex(store.chunks),
    )
    evidence = service.retrieve("What is section 103 of the Sanhita?")
    assert evidence.intent is not None
    assert evidence.results
    assert evidence.results[0].chunk.section_number == "103"
    assert evidence.confidence == 1.0
