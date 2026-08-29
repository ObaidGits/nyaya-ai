"""BM25 sparse retrieval tests (A3-004, A3-008..A3-010; D-013)."""

from app.retrieval.models import MetadataFilter
from app.retrieval.sparse import Bm25SparseIndex, tokenize
from tests.retrieval.fixtures import make_corpus


def _index() -> Bm25SparseIndex:
    return Bm25SparseIndex(make_corpus())


def test_exact_identifier_ranks_section_first() -> None:
    results = _index().search("section 103", None, top_k=5)
    assert results
    assert all(cid.startswith("ts-s103") for cid in results[:2])


def test_legal_identifier_with_subsection_token() -> None:
    assert "103(1)" in tokenize("punishment under section 103(1) of BNS")


def test_lexical_query_finds_matching_chunk() -> None:
    results = _index().search("bail on bond", None, top_k=5)
    assert "ts-s2-001" in results


def test_synonym_query_misses_sparse() -> None:
    # No token overlap -> sparse retrieval legitimately finds nothing.
    results = _index().search("automobile negligence", None, top_k=5)
    assert results == []


def test_metadata_filter_section() -> None:
    results = _index().search("murder", MetadataFilter(section_number="103"), top_k=5)
    assert results
    assert all(cid.startswith("ts-s103") for cid in results)


def test_metadata_filter_act_short() -> None:
    results = _index().search("murder", MetadataFilter(act_short="OTHER"), top_k=5)
    assert results == []


def test_no_results_for_unknown_terms() -> None:
    assert _index().search("zqxjkwv", None, top_k=5) == []


def test_top_k_respected() -> None:
    results = _index().search("offence", None, top_k=2)
    assert len(results) <= 2
