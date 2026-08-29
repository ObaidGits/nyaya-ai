"""Retrieval service tests (A3-003..A3-014; ARCHITECTURE §11-§15)."""

from app.retrieval.dense import CosineDenseIndex
from app.retrieval.models import MetadataFilter, RetrievalRoute
from app.retrieval.service import RetrievalService
from app.retrieval.sparse import Bm25SparseIndex
from app.retrieval.store import ChunkStore
from tests.retrieval.fixtures import DeterministicEmbedder, FakeDenseRetriever, make_corpus


def _service(dense_mapping: dict[str, list[str]] | None = None, **kwargs) -> RetrievalService:
    store = ChunkStore(make_corpus())
    dense = (
        FakeDenseRetriever(dense_mapping)
        if dense_mapping is not None
        else CosineDenseIndex(store.chunks, DeterministicEmbedder())
    )
    return RetrievalService(
        store=store,
        dense=dense,
        sparse=Bm25SparseIndex(store.chunks),
        **kwargs,
    )


def test_exact_section_query_uses_deterministic_lookup() -> None:
    # Dense is scripted to return the WRONG chunk; lookup must win anyway.
    service = _service({"What is section 103 of BNS?": ["ts-s1-001"]})
    evidence = service.retrieve("What is section 103 of BNS?")
    assert evidence.intent is not None
    assert evidence.intent.section_number == "103"
    assert [r.chunk.chunk_id for r in evidence.results] == ["ts-s103-001", "ts-s103-002"]
    assert all(r.source == "lookup" for r in evidence.results)
    assert evidence.confidence == 1.0
    assert evidence.sufficient


def test_lookup_returns_every_part_of_split_section() -> None:
    service = _service()
    evidence = service.retrieve("section 103 BNS")
    assert len(evidence.results) == 2


def test_lookup_for_missing_section_is_insufficient() -> None:
    service = _service()
    evidence = service.retrieve("What is section 999 BNS?")
    assert evidence.results == []
    assert not evidence.sufficient
    assert any("not present" in r for r in evidence.reasons)


def test_natural_language_query_hybrid() -> None:
    # Overlap chunk (ts-s2-001) must outrank a dense-only candidate.
    service = _service({"release on bond": ["ts-s9-001", "ts-s2-001"]})
    evidence = service.retrieve("release on bond")
    ids = [r.chunk.chunk_id for r in evidence.results]
    assert ids[0] == "ts-s2-001"
    assert "ts-s9-001" in ids
    overlap = next(r for r in evidence.results if r.chunk.chunk_id == "ts-s2-001")
    dense_only = next(r for r in evidence.results if r.chunk.chunk_id == "ts-s9-001")
    assert overlap.source == "hybrid"
    assert dense_only.source == "dense"
    assert overlap.dense_rank is not None and overlap.sparse_rank is not None
    assert dense_only.sparse_rank is None


def test_sparse_only_candidate_retrieved() -> None:
    # Dense returns nothing for this query; sparse alone surfaces the chunk.
    service = _service({"bail on bond": []})
    evidence = service.retrieve("bail on bond")
    ids = [r.chunk.chunk_id for r in evidence.results]
    assert "ts-s2-001" in ids
    hit = next(r for r in evidence.results if r.chunk.chunk_id == "ts-s2-001")
    assert hit.source == "sparse"
    assert hit.dense_rank is None


def test_metadata_filter_chapter_restricts_results() -> None:
    service = _service({"murder": ["ts-s2-001"]})
    evidence = service.retrieve("murder", MetadataFilter(chapter="XXVII"))
    assert evidence.results
    assert all(r.chunk.chapter == "XXVII" for r in evidence.results)


def test_metadata_filter_section() -> None:
    service = _service({"offence": ["ts-s1-001"]})
    evidence = service.retrieve("offence", MetadataFilter(section_number="103"))
    assert all(r.chunk.section_number == "103" for r in evidence.results)


def test_metadata_filter_act() -> None:
    service = _service()
    evidence = service.retrieve("murder", MetadataFilter(act="Test Sanhita, 2023"))
    assert evidence.results
    evidence_other = service.retrieve("murder", MetadataFilter(act="Wrong Act"))
    assert evidence_other.results == []


def test_no_results_is_insufficient() -> None:
    service = _service({"zzqqxx": []})
    evidence = service.retrieve("zzqqxx")
    assert evidence.results == []
    assert not evidence.sufficient
    assert evidence.confidence == 0.0


def test_low_confidence_flagged_when_below_threshold() -> None:
    # Single-list top hit normalizes to ~0.5 confidence; threshold 0.9
    # must flag insufficiency instead of silently passing.
    service = _service({"bail": []}, confidence_threshold=0.9)
    evidence = service.retrieve("bail")
    assert evidence.results  # something was retrieved...
    assert not evidence.sufficient  # ...but not confidently enough
    assert any("below threshold" in r for r in evidence.reasons)


def test_threshold_zero_never_refuses() -> None:
    service = _service({"bail": []}, confidence_threshold=0.0)
    evidence = service.retrieve("bail")
    assert evidence.results
    assert evidence.sufficient


def test_document_route_is_honest_stub() -> None:
    service = _service()
    evidence = service.retrieve("What does my notice say?")
    assert evidence.route == RetrievalRoute.DOCUMENT
    assert evidence.results == []
    assert not evidence.sufficient
    assert any("Phase 5" in r for r in evidence.reasons)


def test_combined_route_retrieves_statute_side() -> None:
    service = _service()
    evidence = service.retrieve("Does my notice comply with section 103 BNS?")
    assert evidence.route == RetrievalRoute.COMBINED
    assert evidence.results
    assert evidence.results[0].chunk.section_number == "103"


def test_route_override() -> None:
    service = _service()
    evidence = service.retrieve("bail on bond", route=RetrievalRoute.STATUTE)
    assert evidence.route == RetrievalRoute.STATUTE


def test_lookup_respects_filter() -> None:
    service = _service()
    evidence = service.retrieve("What is section 103 BNS?", MetadataFilter(section_number="1"))
    assert evidence.results == []  # filter wins over lookup intent
