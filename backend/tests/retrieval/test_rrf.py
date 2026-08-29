"""RRF fusion tests (A3-005, A3-006, A3-007; D-014)."""

from app.retrieval.rrf import RRF_K, rrf_fuse


def test_overlap_candidate_ranks_first() -> None:
    fused = rrf_fuse(["a", "b", "c"], ["b", "d", "e"])
    assert fused[0][0] == "b"  # present in both lists


def test_rrf_score_matches_formula() -> None:
    fused = rrf_fuse(["a"], ["a"])
    expected = 2.0 / (RRF_K + 1)
    assert abs(fused[0][1] - expected) < 1e-12


def test_higher_rank_wins() -> None:
    # "a" rank 1 dense vs "b" rank 1 sparse; both lists agree a > b overall.
    fused = rrf_fuse(["a", "b"], ["a", "b"])
    assert [cid for cid, _ in fused] == ["a", "b"]


def test_disjoint_lists_fused_in_order() -> None:
    fused = rrf_fuse(["a", "b"], ["c", "d"])
    ids = {cid for cid, _ in fused}
    assert ids == {"a", "b", "c", "d"}


def test_empty_lists() -> None:
    assert rrf_fuse([], []) == []


def test_deterministic_tie_break() -> None:
    first = rrf_fuse(["x", "y"], ["y", "x"])
    second = rrf_fuse(["x", "y"], ["y", "x"])
    assert first == second


def test_custom_k_changes_score() -> None:
    assert rrf_fuse(["a"], [], k=10)[0][1] != rrf_fuse(["a"], [], k=100)[0][1]
