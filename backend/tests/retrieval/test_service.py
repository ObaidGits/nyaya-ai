"""Retrieval service tests (A3-003..A3-014; ARCHITECTURE §11-§15)."""

from app.documents.models import DocumentEvidence, DocumentHit
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


def test_document_route_fails_closed_without_session() -> None:
    """Document route without a session id or index yields no evidence (§21)."""
    service = _service()
    evidence = service.retrieve("What does my notice say?")
    assert evidence.route == RetrievalRoute.DOCUMENT
    assert evidence.results == []
    assert evidence.document_hits == []
    assert not evidence.sufficient
    assert any("session" in r for r in evidence.reasons)


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


# ---------------------------------------------------------------------------
# Session document retrieval confidence gate (§15, §34)
# ---------------------------------------------------------------------------


class _StubDocumentRetrieval:
    """Scripted session document retrieval returning fixed hits."""

    def __init__(self, hits: list[DocumentHit]) -> None:
        self._hits = hits

    def retrieve(self, session_id: str, query: str) -> DocumentEvidence:
        return DocumentEvidence(hits=self._hits)


def _doc_hit(score: float) -> DocumentHit:
    return DocumentHit(
        chunk_id="d-p0001-000",
        document_id="d31f",
        text="Legal notice: the tenant must vacate the premises within thirty days.",
        page_start=1,
        page_end=1,
        score=score,
    )


def test_document_route_sufficient_when_confidence_meets_threshold() -> None:
    service = _service(document_retrieval=_StubDocumentRetrieval([_doc_hit(0.9)]))
    evidence = service.retrieve("What does my notice say?", session_id="sess-1")
    assert evidence.route == RetrievalRoute.DOCUMENT
    assert evidence.document_hits
    assert evidence.sufficient


def test_document_route_refuses_when_confidence_below_threshold() -> None:
    """Low-similarity document hits are not sufficient evidence: the gate
    fails closed instead of letting weak overlap ground an answer."""
    service = _service(
        document_retrieval=_StubDocumentRetrieval([_doc_hit(0.05)]),
        document_confidence_threshold=0.1,
    )
    evidence = service.retrieve("What does my notice say?", session_id="sess-1")
    assert evidence.document_hits  # hits exist...
    assert not evidence.sufficient  # ...but confidence gates them out
    assert any("below threshold" in r for r in evidence.reasons)


def test_document_gate_default_calibrated_to_hashing_cosine_scale() -> None:
    """Default threshold regression (D-073): HashingEmbedder cosines are
    structurally low — a genuinely matching notice chunk scores ~0.08 — so
    the default gate must sit below that (0.05) while still rejecting
    near-zero junk overlap."""
    # Real-match-level score passes with the default threshold.
    passing = _service(document_retrieval=_StubDocumentRetrieval([_doc_hit(0.08)]))
    evidence = passing.retrieve("What does my notice say?", session_id="sess-1")
    assert evidence.sufficient
    # Junk-level overlap is still gated out by the same default.
    junk = _service(document_retrieval=_StubDocumentRetrieval([_doc_hit(0.02)]))
    evidence = junk.retrieve("What does my notice say?", session_id="sess-1")
    assert not evidence.sufficient


# ---------------------------------------------------------------------------
# Act-mismatch fallback guard (D-071)
# ---------------------------------------------------------------------------


def test_act_alias_fallback_in_single_act_corpus() -> None:
    """Single-act corpus: the user's act label is an alias, so the lookup
    retries without the act restriction."""
    service = _service()
    evidence = service.retrieve("What is section 103 of BNS?")
    assert evidence.results  # TS corpus answers the BNS-labelled query


def test_act_mismatch_refuses_in_multi_act_corpus() -> None:
    """Multi-act corpus: a missing act must not silently substitute another
    act's text for the requested authority."""

    def with_act(act_short: str) -> list:
        return [
            c.model_copy(update={"act_short": act_short, "act": f"Act {act_short}"})
            for c in make_corpus()
        ]

    chunks = [*with_act("BNSS"), *with_act("TS")]
    store = ChunkStore(chunks)
    service = RetrievalService(
        store=store,
        dense=FakeDenseRetriever({}),
        sparse=Bm25SparseIndex(chunks),
    )
    evidence = service.retrieve("What is section 103 of BNS?")
    assert evidence.results == []
    assert not evidence.sufficient
    assert any("BNS" in r for r in evidence.reasons)
