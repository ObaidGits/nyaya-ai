"""BM25 sparse retrieval tests (A3-004, A3-008..A3-010; D-013)."""

import pytest
from app.retrieval.models import MetadataFilter
from app.retrieval.sparse import Bm25SparseIndex, tokenize
from tests.retrieval.fixtures import make_corpus


def _index() -> Bm25SparseIndex:
    return Bm25SparseIndex(make_corpus())


@pytest.mark.parametrize(
    ("surface", "stem"),
    [
        ("murder", "murder"),
        ("murders", "murder"),
        ("murderer", "murderer"),
        ("murdering", "murder"),
        ("murdered", "murder"),
        ("killing", "murder"),
        ("punishment", "punishment"),
        ("punished", "punish"),
        ("cheating", "cheat"),
        ("cheated", "cheat"),
        ("notices", "notic"),
        ("notice", "notic"),
        ("cruelty", "cruelty"),
        ("cases", "cas"),
        ("witnesses", "witness"),
        ("committed", "commit"),
        ("deliberately", "deliberat"),
        ("103", "103"),
        ("103(1)", "103(1)"),
    ],
)
def test_stemming_is_symmetric_and_conservative(surface: str, stem: str) -> None:
    # Symmetric light stemmer: inflected surface forms collapse onto the
    # same stem as the statutory wording they match.
    assert tokenize(surface) == [stem]


def test_stopwords_dropped() -> None:
    assert tokenize("What is the punishment for murder?") == ["punishment", "murder"]


@pytest.mark.parametrize(
    ("surface", "normalized"),
    [
        ("killed", "murder"),
        ("kill", "murder"),
        ("thief", "theft"),
        ("steal", "theft"),
        ("stole", "theft"),
        ("stolen", "theft"),
        ("cruel", "cruelty"),
    ],
)
def test_layman_to_statutory_vocabulary_map(surface: str, normalized: str) -> None:
    # High-frequency layman words map to the statutory term the corpus
    # actually uses (each entry is documented in sparse.py).
    assert tokenize(surface) == [normalized]


def test_exact_identifier_ranks_section_first() -> None:
    results = _index().search("section 103", None, top_k=5)
    assert results
    assert all(cid.startswith("ts-s103") for cid in results[:2])


def test_legal_identifier_with_subsection_token() -> None:
    assert "103(1)" in tokenize("punishment under section 103(1) of BNS")


def test_lexical_query_finds_matching_chunk() -> None:
    results = _index().search("bail on bond", None, top_k=5)
    assert "ts-s2-001" in results


def test_indirect_wording_finds_section() -> None:
    # "killed" shares no surface token with "murder"; the layman→statutory
    # map plus stemming must still rank the murder section first.
    results = _index().search("Someone killed a person deliberately", None, top_k=5)
    assert results
    assert results[0].startswith("ts-s103")


def test_inflected_wording_finds_section() -> None:
    # "punished"/"cheating" style inflections collapse via stemming.
    results = _index().search("Whoever commits murders shall be punished", None, top_k=5)
    assert results
    assert results[0].startswith("ts-s103")


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
