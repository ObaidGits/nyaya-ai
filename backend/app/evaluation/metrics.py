"""Evaluation metrics (IMPLEMENTATION_PLAN §8.3-§8.5, F-008..F-017).

Pure functions so scoring is deterministic and unit-testable; the runner
composes them.
"""

from __future__ import annotations

from math import ceil


def recall_at_k(retrieved_sections: list[str], expected_sections: list[str], k: int) -> float:
    """Fraction of expected sections present in the first ``k`` results."""
    if not expected_sections:
        return 0.0
    top = retrieved_sections[:k]
    hits = sum(1 for section in expected_sections if section in top)
    return hits / len(expected_sections)


def reciprocal_rank(retrieved_sections: list[str], expected_sections: list[str]) -> float:
    """1/rank of the first relevant result, 0 when nothing relevant retrieved."""
    for rank, section in enumerate(retrieved_sections, start=1):
        if section in expected_sections:
            return 1.0 / rank
    return 0.0


def mean(values: list[float]) -> float:
    """Arithmetic mean, 0.0 for an empty list (never a silent divide-by-zero)."""
    return sum(values) / len(values) if values else 0.0


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile (``p`` in 0..100); 0.0 for empty input."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, ceil(p / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def citation_accuracy(
    cited_sections: list[str], retrieved_sections: list[str], expected_sections: list[str]
) -> float:
    """Precision of citations in one answer (F-011..F-013).

    A citation is correct when the cited section is present in the retrieved
    context (supported) AND relevant: either it is an expected golden section
    or the retrieval pipeline itself ranked it as evidence. Unsupported
    citations score 0 — they are exactly what the citation guard must remove.
    """
    if not cited_sections:
        return 0.0
    relevant = set(expected_sections) | set(retrieved_sections)
    correct = sum(1 for section in cited_sections if section in relevant)
    return correct / len(cited_sections)


def refusal_correctness(refused: bool, must_refuse: bool) -> float:
    """1.0 when refusal behavior matches the expectation, else 0.0 (F-014)."""
    return 1.0 if refused == must_refuse else 0.0
