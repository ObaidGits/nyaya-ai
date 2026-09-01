"""Golden-set loading and validation tests (F-001..F-007)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.evaluation.golden import load_golden_set

REPO_ROOT = Path(__file__).resolve().parents[3]


def _write(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return path


def test_shipped_golden_set_loads_and_is_in_range() -> None:
    cases = load_golden_set(REPO_ROOT / "eval" / "golden_set.jsonl")
    assert 25 <= len(cases) <= 30


def test_shipped_golden_set_has_required_mix() -> None:
    cases = load_golden_set(REPO_ROOT / "eval" / "golden_set.jsonl")
    refused = [case for case in cases if case.must_refuse]
    assert len(refused) >= 5
    assert all(not case.expected_sections for case in refused)
    assert any(case.type == "lookup" for case in cases)
    assert any(case.type in ("semantic", "reasoning") for case in cases)
    assert any(case.type == "document" for case in cases)
    assert any(case.type == "combined" for case in cases)
    # Every in-scope statute case carries expected sections (F-003).
    in_scope = [c for c in cases if not c.must_refuse and c.type not in ("document",)]
    assert all(c.expected_sections or c.type == "combined" for c in in_scope)


def test_every_case_tied_to_dev_corpus() -> None:
    cases = load_golden_set(REPO_ROOT / "eval" / "golden_set.jsonl")
    assert all(case.corpus == "bnss-dev" for case in cases)


def test_shipped_bns_golden_set_loads_with_required_mix() -> None:
    # D-093: the real-BNS golden set. Expected sections must exist in the
    # serving corpus artifact, so a corpus regression that renumbers or
    # loses sections fails here rather than silently skewing eval numbers.
    cases = load_golden_set(REPO_ROOT / "eval" / "golden_set_bns.jsonl")
    assert all(case.corpus == "bns" for case in cases)
    types = {case.type for case in cases}
    assert {"lookup", "semantic", "reasoning"} <= types
    refused = [case for case in cases if case.must_refuse]
    assert len(refused) >= 5
    assert all(not case.expected_sections for case in refused)

    corpus_sections: set[str] = set()
    corpus_path = REPO_ROOT / "data" / "processed" / "bns_corpus.jsonl"
    if corpus_path.exists():  # artifact is built by bootstrap, not committed
        for line in corpus_path.read_text().splitlines():
            if line.strip():
                corpus_sections.add(json.loads(line)["section_number"])
        expected = {section for case in cases for section in case.expected_sections}
        missing = sorted(expected - corpus_sections, key=lambda s: int(s))
        assert not missing, f"golden set references missing corpus sections: {missing}"


def test_too_few_questions_rejected(tmp_path: Path) -> None:
    rows = [
        {"id": f"c{i}", "question": f"q{i}", "type": "semantic", "expected_sections": ["1"]}
        for i in range(10)
    ]
    with pytest.raises(ValueError, match="25-30"):
        load_golden_set(_write(tmp_path / "small.jsonl", rows))


def test_too_few_refusal_cases_rejected(tmp_path: Path) -> None:
    rows = [
        {"id": f"c{i}", "question": f"q{i}", "type": "semantic", "expected_sections": ["1"]}
        for i in range(25)
    ]
    with pytest.raises(ValueError, match="out-of-scope"):
        load_golden_set(_write(tmp_path / "norefuse.jsonl", rows))


def test_refusal_case_with_sections_rejected(tmp_path: Path) -> None:
    rows = (
        [
            {"id": f"c{i}", "question": f"q{i}", "type": "semantic", "expected_sections": ["1"]}
            for i in range(24)
        ]
        + [
            {"id": f"o{i}", "question": f"q{i}", "type": "semantic", "must_refuse": True}
            for i in range(5)
        ]
        + [
            {
                "id": "o",
                "question": "q",
                "type": "semantic",
                "must_refuse": True,
                "expected_sections": ["9"],
            }
        ]
    )
    with pytest.raises(ValueError, match="no expected sections"):
        load_golden_set(_write(tmp_path / "bad.jsonl", rows))


def test_malformed_row_reports_line_number(tmp_path: Path) -> None:
    path = tmp_path / "broken.jsonl"
    path.write_text('{"id": "a", "question": "q", "type": "nonsense"}\n')
    with pytest.raises(ValueError, match="row 1"):
        load_golden_set(path)
