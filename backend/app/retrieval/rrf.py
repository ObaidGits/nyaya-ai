"""Reciprocal Rank Fusion (DECISIONS.md D-014, ARCHITECTURE §11).

``score(d) = sum over rank lists of 1 / (k + rank(d))`` with rank starting
at 1 and k = 60 (initial tunable value, D-015).
"""

from __future__ import annotations

RRF_K = 60


def rrf_fuse(
    dense_ids: list[str],
    sparse_ids: list[str],
    *,
    k: int = RRF_K,
) -> list[tuple[str, float]]:
    """Fuse two ranked id lists into one unified ranking.

    Returns ``(chunk_id, rrf_score)`` pairs sorted by descending score,
    ties broken by first appearance (deterministic ordering).
    """
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for ranked in (dense_ids, sparse_ids):
        for rank, chunk_id in enumerate(ranked, start=1):
            if chunk_id not in scores:
                first_seen[chunk_id] = len(first_seen)
                scores[chunk_id] = 0.0
            scores[chunk_id] += 1.0 / (k + rank)
    ranked_pairs = sorted(
        ((cid, scores[cid]) for cid in scores),
        key=lambda pair: (-pair[1], first_seen[pair[0]]),
    )
    return ranked_pairs
