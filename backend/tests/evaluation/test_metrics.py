"""Unit tests for the evaluation metric functions (F-008..F-017)."""

from __future__ import annotations

from app.evaluation.metrics import (
    citation_accuracy,
    mean,
    percentile,
    recall_at_k,
    reciprocal_rank,
    refusal_correctness,
)


class TestRecall:
    def test_recall_at_5_counts_expected_in_top_k(self) -> None:
        retrieved = ["1", "2", "3", "4", "5", "6", "7"]
        assert recall_at_k(retrieved, ["2", "7"], 5) == 0.5

    def test_recall_ignores_expected_beyond_k(self) -> None:
        retrieved = ["1", "2", "3", "4", "5"]
        assert recall_at_k(retrieved, ["5", "6"], 5) == 0.5

    def test_recall_perfect_when_all_expected_present(self) -> None:
        assert recall_at_k(["224", "230"], ["224", "230"], 10) == 1.0

    def test_recall_zero_when_nothing_matches(self) -> None:
        assert recall_at_k(["1", "2"], ["224"], 5) == 0.0

    def test_recall_zero_without_expectations(self) -> None:
        assert recall_at_k(["1"], [], 5) == 0.0


class TestMRR:
    def test_reciprocal_rank_first_position(self) -> None:
        assert reciprocal_rank(["224", "230"], ["224"]) == 1.0

    def test_reciprocal_rank_third_position(self) -> None:
        assert reciprocal_rank(["1", "2", "224"], ["224"]) == 1 / 3

    def test_reciprocal_rank_absent(self) -> None:
        assert reciprocal_rank(["1", "2"], ["224"]) == 0.0


class TestCitationAccuracy:
    def test_supported_and_expected_citation_scores_full(self) -> None:
        assert citation_accuracy(["224"], ["224", "230"], ["224"]) == 1.0

    def test_citation_in_retrieved_context_but_not_expected_is_accepted(self) -> None:
        # Relevance (F-013): the pipeline itself surfaced the section.
        assert citation_accuracy(["230"], ["230"], ["224"]) == 1.0

    def test_unsupported_citation_scores_zero(self) -> None:
        # Presence check (F-012): section absent from retrieved context.
        assert citation_accuracy(["999"], ["224"], ["224"]) == 0.0

    def test_mixed_citations_score_fraction(self) -> None:
        assert citation_accuracy(["224", "999"], ["224"], ["224"]) == 0.5

    def test_no_citations_score_zero(self) -> None:
        assert citation_accuracy([], ["224"], ["224"]) == 0.0


class TestRefusalCorrectness:
    def test_refused_when_expected(self) -> None:
        assert refusal_correctness(refused=True, must_refuse=True) == 1.0

    def test_answered_when_expected(self) -> None:
        assert refusal_correctness(refused=False, must_refuse=False) == 1.0

    def test_wrong_refusal(self) -> None:
        assert refusal_correctness(refused=True, must_refuse=False) == 0.0

    def test_wrong_answer(self) -> None:
        assert refusal_correctness(refused=False, must_refuse=True) == 0.0


class TestLatencyHelpers:
    def test_percentile_p50_and_p95(self) -> None:
        values = [float(i) for i in range(1, 101)]  # 1..100
        assert percentile(values, 50) == 50.0
        assert percentile(values, 95) == 95.0

    def test_percentile_empty_is_zero(self) -> None:
        assert percentile([], 95) == 0.0

    def test_mean(self) -> None:
        assert mean([1.0, 2.0, 3.0]) == 2.0

    def test_mean_empty_is_zero(self) -> None:
        assert mean([]) == 0.0
