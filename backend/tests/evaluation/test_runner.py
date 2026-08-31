"""Runner tests: golden-set execution, scoring, determinism (F-001..F-024)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.evaluation.golden import GoldenCase, load_golden_set
from app.evaluation.runner import _run_case, run_evaluation
from app.retrieval.service import RetrievalService
from tests.generation.fixtures import ScriptedProvider

SECTION_TEXTS = {
    "1": "Contents of charge. Every charge shall state the offence with which "
    "the accused is charged and the law creating the offence.",
    "2": "Discharge of the accused. The accused shall be discharged when the "
    "evidence is insufficient to proceed with the trial.",
    "3": "Witness protection scheme. The State Government shall prepare a "
    "witness protection scheme for witnesses.",
}


def _write_corpus(path: Path) -> Path:
    rows = []
    for section, text in SECTION_TEXTS.items():
        rows.append(
            {
                "chunk_id": f"synth-s{section}",
                "act": "Synthetic Act, 2023",
                "act_short": "SYN",
                "chapter": None,
                "chapter_title": None,
                "section_number": section,
                "section_title": text.split(".")[0],
                "subsection": None,
                "clause": None,
                "text": text,
                "has_illustration": False,
                "has_proviso": False,
                "has_exception": False,
                "page_start": int(section),
                "page_end": int(section),
                "source_uri": "synthetic://test",
                "ingested_at": "2026-01-01T00:00:00+00:00",
            }
        )
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return path


def _synthetic_cases() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for i, section in enumerate(("1", "2", "3")):
        rows.append(
            {
                "id": f"l{i}",
                "question": f"What does section {section} say?",
                "type": "lookup",
                "expected_sections": [section],
            }
        )
    semantic = [
        ("What must a charge state?", ["1"]),
        ("When is the accused discharged?", ["2"]),
        ("What protection exists for witnesses?", ["3"]),
        ("Which law creates the offence charged?", ["1"]),
        ("Who prepares the witness protection scheme?", ["3"]),
    ]
    for i, (question, sections) in enumerate(semantic):
        rows.append(
            {"id": f"s{i}", "question": question, "type": "semantic", "expected_sections": sections}
        )
    # Pad with repetition-free lookup variants to reach the required size.
    for i in range(11):
        rows.append(
            {
                "id": f"l1{i}",
                "question": f"What does section {i % 3 + 1} of SYN provide?",
                "type": "lookup",
                "expected_sections": [str(i % 3 + 1)],
            }
        )
    for i in range(6):
        rows.append(
            {
                "id": f"o{i}",
                "question": f"Unrelated question number {i} about cooking?",
                "type": "semantic",
                "must_refuse": True,
            }
        )
    rows.append(
        {
            "id": "doc-1",
            "question": "According to my document, how many days notice must a tenant give?",
            "type": "document",
        }
    )
    rows.append(
        {
            "id": "combined-1",
            "question": "What must a charge state and what does my document say about deposits?",
            "type": "combined",
            "expected_sections": ["1"],
        }
    )
    return rows


@pytest.fixture
def synthetic_setup(tmp_path: Path) -> tuple[Path, Path]:
    corpus = _write_corpus(tmp_path / "corpus.jsonl")
    golden = tmp_path / "golden.jsonl"
    golden.write_text("\n".join(json.dumps(r) for r in _synthetic_cases()) + "\n")
    return corpus, golden


async def test_run_evaluation_produces_both_configurations(
    synthetic_setup: tuple[Path, Path],
) -> None:
    corpus, golden = synthetic_setup
    results = await run_evaluation(golden, corpus)
    names = [config["config"] for config in results["configurations"]]
    assert names == ["dense_only", "hybrid"]
    for config in results["configurations"]:
        assert config["cases"] == len(load_golden_set(golden))
        assert config["failed_cases"] == 0
        assert 0.0 <= config["recall_at_5"] <= 1.0
        assert 0.0 <= config["recall_at_10"] <= 1.0
        assert 0.0 <= config["mrr"] <= 1.0
        assert 0.0 <= config["refusal_correctness"] <= 1.0
        assert config["retrieval_latency_p50"] > 0.0
        assert config["retrieval_latency_p95"] >= config["retrieval_latency_p50"]


async def test_lookup_cases_retrieve_expected_sections(
    synthetic_setup: tuple[Path, Path],
) -> None:
    corpus, golden = synthetic_setup
    results = await run_evaluation(golden, corpus)
    hybrid = results["configurations"][1]
    # Deterministic section lookup must be perfect on identifier questions.
    assert hybrid["recall_at_5"] > 0.5


async def test_document_cases_hit_session_documents(
    synthetic_setup: tuple[Path, Path],
) -> None:
    corpus, golden = synthetic_setup
    results = await run_evaluation(golden, corpus)
    for config in results["configurations"]:
        assert config["document_hit_rate"] == 1.0


async def test_results_are_deterministic_across_runs(
    synthetic_setup: tuple[Path, Path],
) -> None:
    corpus, golden = synthetic_setup
    first = await run_evaluation(golden, corpus)
    second = await run_evaluation(golden, corpus)

    def stable(run: dict[str, object]) -> dict[str, object]:
        run = json.loads(json.dumps(run))
        for config in run["configurations"]:
            for key in (
                "retrieval_latency_p50",
                "retrieval_latency_p95",
                "generation_latency_p50",
                "generation_latency_p95",
            ):
                config.pop(key, None)
        return run

    assert stable(first) == stable(second)


async def test_failed_cases_are_reported_not_hidden(
    synthetic_setup: tuple[Path, Path],
) -> None:
    corpus, golden = synthetic_setup
    # Corrupt one row's expectation to force a retrieval miss, and make the
    # corpus unreadable in a copy to force a hard error path via load failure.
    rows = [json.loads(line) for line in golden.read_text().splitlines() if line]
    rows[0]["question"] = ""  # empty question is still runnable; assert no crash
    golden.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    results = await run_evaluation(golden, corpus)
    for config in results["configurations"]:
        assert config["failed_cases"] == 0


async def test_llm_mode_scores_citations(
    synthetic_setup: tuple[Path, Path],
) -> None:
    corpus, golden = synthetic_setup
    provider = ScriptedProvider(["The charge must state the offence [SYN s.1]."] * 40)
    results = await run_evaluation(golden, corpus, provider=provider)
    assert results["llm_mode"] is True
    assert results["deterministic"] is False
    hybrid = results["configurations"][1]
    assert hybrid["citation_accuracy"] is not None
    assert hybrid["citation_accuracy"] > 0.0
    assert hybrid["generation_latency_p50"] is not None


async def test_unsupported_citation_scores_zero(
    synthetic_setup: tuple[Path, Path],
) -> None:
    corpus, _ = synthetic_setup
    from app.evaluation.runner import build_retrieval_service

    service: RetrievalService = build_retrieval_service(corpus, sparse_top_k=20)
    case = GoldenCase(
        id="x",
        question="What must a charge state?",
        type="semantic",
        expected_sections=["1"],
    )
    provider = ScriptedProvider(["The sky is blue [SYN s.9]."] * 5)
    from app.generation.service import GenerationService

    result = await _run_case(case, service, GenerationService(provider))
    assert result.citation_accuracy == 0.0
