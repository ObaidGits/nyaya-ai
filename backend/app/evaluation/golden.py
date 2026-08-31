"""Golden-set loading and validation (F-001..F-007)."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class GoldenCase(BaseModel):
    """One evaluation question with its corpus-grounded expectation.

    ``corpus`` ties the case to a specific development corpus revision so
    expectations are never mistaken for portable legal facts.
    """

    id: str
    question: str
    type: str = Field(pattern="^(lookup|semantic|reasoning|identifier|document|combined)$")
    expected_sections: list[str] = Field(default_factory=list)
    must_refuse: bool = False
    corpus: str = "bnss-dev"


def load_golden_set(path: Path) -> list[GoldenCase]:
    """Load and validate the golden set; raises on malformed rows."""
    cases: list[GoldenCase] = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(GoldenCase.model_validate(json.loads(line)))
        except Exception as exc:
            raise ValueError(f"golden set row {line_number} is invalid: {exc}") from exc
    if not (25 <= len(cases) <= 30):
        raise ValueError(f"golden set must contain 25-30 questions, found {len(cases)}")
    refused = [case for case in cases if case.must_refuse]
    if len(refused) < 5:
        raise ValueError(f"golden set needs at least 5 out-of-scope cases, found {len(refused)}")
    for case in refused:
        if case.expected_sections:
            raise ValueError(f"out-of-scope case {case.id} must have no expected sections")
    return cases
