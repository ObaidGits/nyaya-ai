"""Retrieval service tests (A3-003..A3-014; ARCHITECTURE §11-§15)."""

import pytest
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


# -- semantic-relevance confidence gate (remediation) -----------------------


class _ScoredDenseRetriever(FakeDenseRetriever):
    """Fake dense retriever with a scripted top cosine similarity.

    Exercises the relevance gate without a real embedding model.
    """

    def __init__(self, mapping: dict[str, list[str]], similarity: float) -> None:
        super().__init__(mapping)
        self._similarity = similarity

    def top_similarity(self, query: str, flt: MetadataFilter | None) -> float:
        return self._similarity


def test_relevance_gate_scales_confidence() -> None:
    """Top cosine between floor (0.48) and saturation (0.60) multiplies the
    RRF confidence by the linear relevance factor."""
    # ts-s2-001 ranks first in BOTH lists → RRF confidence 1.0.
    store = ChunkStore(make_corpus())
    service = RetrievalService(
        store=store,
        dense=_ScoredDenseRetriever({"release on bond": ["ts-s2-001", "ts-s9-001"]}, 0.54),
        sparse=Bm25SparseIndex(store.chunks),
    )
    evidence = service.retrieve("release on bond")
    assert evidence.sufficient
    # (0.54 - 0.48) / (0.60 - 0.48) == 0.5, applied to a 1.0 RRF confidence.
    assert evidence.confidence == pytest.approx(0.5)
    assert any("semantic relevance factor 0.500" in r for r in evidence.reasons)


def test_below_relevance_floor_refuses() -> None:
    """Top cosine at/below the floor zeroes confidence: confidently
    irrelevant evidence (RRF overlap ~1.0) must not be answered."""
    store = ChunkStore(make_corpus())
    service = RetrievalService(
        store=store,
        dense=_ScoredDenseRetriever({"release on bond": ["ts-s2-001"]}, 0.40),
        sparse=Bm25SparseIndex(store.chunks),
    )
    evidence = service.retrieve("release on bond")
    assert evidence.confidence == 0.0
    assert not evidence.sufficient


def test_relevance_saturation_is_unity() -> None:
    store = ChunkStore(make_corpus())
    service = RetrievalService(
        store=store,
        dense=_ScoredDenseRetriever({"release on bond": ["ts-s2-001", "ts-s9-001"]}, 0.90),
        sparse=Bm25SparseIndex(store.chunks),
    )
    evidence = service.retrieve("release on bond")
    assert evidence.confidence == pytest.approx(1.0)


def test_dense_retriever_without_similarity_signal_keeps_rrf_confidence() -> None:
    """Legacy doubles without top_similarity: the gate is skipped and the
    confidence stays the pure RRF-overlap signal (backward compatible)."""
    store = ChunkStore(make_corpus())
    service = RetrievalService(
        store=store,
        dense=FakeDenseRetriever({"release on bond": ["ts-s2-001", "ts-s9-001"]}),
        sparse=Bm25SparseIndex(store.chunks),
    )
    evidence = service.retrieve("release on bond")
    assert evidence.sufficient
    assert evidence.confidence == pytest.approx(1.0)


# -- foreign-statute out-of-scope detection (remediation) --------------------


def test_foreign_statute_query_refuses() -> None:
    """A question about a statute the corpus does not hold fails closed —
    no look-alike sections from the indexed act (SRC-013 metadata-driven)."""
    service = _service()
    evidence = service.retrieve("How do I file for divorce under the Hindu Marriage Act?")
    assert evidence.results == []
    assert not evidence.sufficient
    assert evidence.confidence == 0.0
    assert any("Hindu Marriage Act" in r for r in evidence.reasons)


def test_foreign_statute_lookup_refuses() -> None:
    """Even a section-number query about a foreign act must not substitute
    the indexed act's same-numbered section."""
    service = _service()
    evidence = service.retrieve("What does section 103 of the Hindu Marriage Act say?")
    assert evidence.results == []
    assert not evidence.sufficient


def test_corpus_act_mention_is_not_foreign() -> None:
    """Naming the indexed act (or a word of its title) keeps normal
    retrieval; the check must not refuse legitimate in-corpus questions."""
    service = _service()
    evidence = service.retrieve("What does the Test Sanhita Act say about release on bond?")
    # Not refused by the foreign-statute gate: retrieval actually ran.
    assert all("not the indexed corpus" not in r for r in evidence.reasons)
    assert any("retrieved" in r for r in evidence.reasons)


def test_constitution_and_amendment_mentions_refuse() -> None:
    service = _service()
    for query in (
        "What does the Fourth Amendment of the US Constitution protect?",
        "What are the sections of the Indian Contract Act governing bailment?",
    ):
        evidence = service.retrieve(query)
        assert not evidence.sufficient, query
        assert evidence.results == [], query


# ---------------------------------------------------------------------------
# Document fallback for statute-routed queries (§14 remediation)
# ---------------------------------------------------------------------------


def test_statute_route_falls_back_to_session_documents() -> None:
    """A query the keyword router sends STATUTE ("What is the filing date in
    the writ petition?" — no recognized artifact noun at the time) whose
    statute evidence is insufficient must fall through to the session's
    documents before refusing. Live regression: the writ-petition question
    was routed statute, retrieved irrelevant BNS chunks, and refused while
    the uploaded document was READY."""
    service = _service(document_retrieval=_StubDocumentRetrieval([_doc_hit(0.9)]))
    # "zzqqxx" matches nothing in the statute corpus → insufficient.
    evidence = service.retrieve("zzqqxx about the filing date", session_id="sess-1")
    assert evidence.document_hits
    assert evidence.sufficient
    assert evidence.route == RetrievalRoute.STATUTE
    assert any("session documents retrieved" in r for r in evidence.reasons)


def test_statute_route_no_fallback_without_session() -> None:
    """No session id → no document fallback: the insufficient statute
    evidence refuses honestly (fail closed, §21)."""
    service = _service(document_retrieval=_StubDocumentRetrieval([_doc_hit(0.9)]))
    evidence = service.retrieve("zzqqxx about the filing date")
    assert evidence.document_hits == []
    assert not evidence.sufficient


def test_statute_route_no_fallback_when_documents_weak() -> None:
    """Document hits below the confidence gate do not rescue the turn:
    the fallback only substitutes genuinely sufficient document evidence."""
    service = _service(
        document_retrieval=_StubDocumentRetrieval([_doc_hit(0.01)]),
        document_confidence_threshold=0.1,
    )
    evidence = service.retrieve("zzqqxx about the filing date", session_id="sess-1")
    assert not evidence.sufficient


def test_statute_route_no_fallback_when_statute_sufficient() -> None:
    """Sufficient statute evidence never triggers document retrieval: the
    fallback must not widen retrieval for in-scope statute questions."""
    class _Fails:
        def retrieve(self, session_id: str, query: str) -> DocumentEvidence:
            raise AssertionError("document retrieval must not run")

    service = _service(document_retrieval=_Fails())
    # Deterministic section lookup: sufficient by construction (conf 1.0).
    evidence = service.retrieve("What is section 103 BNS?", session_id="sess-1")
    assert evidence.results
    assert evidence.sufficient


def test_statute_route_fallback_keeps_foreign_statute_fail_closed() -> None:
    """A query naming a statute the corpus cannot ground still refuses even
    with session documents available: the fallback must not answer a Hindu
    Marriage Act question from the user's upload silently. Document hits
    below the gate stay insufficient."""
    service = _service(document_retrieval=_StubDocumentRetrieval([_doc_hit(0.01)]))
    evidence = service.retrieve("What does the Hindu Marriage Act say?", session_id="sess-1")
    assert not evidence.sufficient
    assert evidence.document_hits == []


# ---------------------------------------------------------------------------
# Foreign statute gate: case-insensitivity + jurisdictions (M2 audit)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "What is the punishment of murder in New York?",
        "What is the punishment for murder in new york?",
        "How does california law punish theft?",
        "what does the hindu marriage act say about divorce?",
        "What are the sections of the Code of Civil Procedure for bailment?",
    ],
)
def test_foreign_statute_gate_case_insensitive_and_jurisdictions(query: str) -> None:
    """Lowercase statute names, connective-lowercase act names ("Code of
    Civil Procedure"), and bare foreign jurisdictions ("murder in New
    York") all fail closed with the not-the-indexed-corpus reason, so the
    contextual refusal can say WHY (BNS does not cover New York law)."""
    service = _service()
    evidence = service.retrieve(query)
    assert not evidence.sufficient, query
    assert evidence.results == [], query
    assert any("not the indexed corpus" in r for r in evidence.reasons), query


@pytest.mark.parametrize(
    "query",
    [
        "What is the punishment for murder in India?",
        "What does the Bharatiya Nyaya Sanhita say about murder?",
        "Is a notice period required under the Test Sanhita Act?",
    ],
)
def test_indian_references_are_not_foreign(query: str) -> None:
    service = _service()
    evidence = service.retrieve(query)
    assert all("not the indexed corpus" not in r for r in evidence.reasons), query


# ---------------------------------------------------------------------------
# Subsection narrowing on deterministic lookup (M3 audit)
# ---------------------------------------------------------------------------


def test_subsection_lookup_prefers_subsection_chunk() -> None:
    """A lookup naming a subsection returns the chunks for that exact
    subsection, not the whole section's other parts."""
    service = _service()
    evidence = service.retrieve("What does section 103(1) of BNS say?")
    assert evidence.results
    assert all(r.chunk.chunk_id == "ts-s103-002" for r in evidence.results)


def test_subsection_lookup_falls_back_to_whole_section() -> None:
    """No chunk for the named subsection (corpus splits the section
    differently): the whole-section chunks ground the answer."""
    service = _service()
    evidence = service.retrieve("What does section 103(2) of BNS say?")
    assert evidence.results
    assert {r.chunk.chunk_id for r in evidence.results} == {"ts-s103-001", "ts-s103-002"}


def test_whole_section_lookup_unnarrowed() -> None:
    service = _service()
    evidence = service.retrieve("section 103 BNS")
    assert {r.chunk.chunk_id for r in evidence.results} == {"ts-s103-001", "ts-s103-002"}
