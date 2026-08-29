"""Source and structure validation (SRC-002/SRC-003, replaceable source).

The pipeline must verify that the supplied source actually contains the
expected corpus **before** treating the ingestion as authoritative.
Validation is content-based (detected act title, structural invariants);
the filename is never used as evidence.
"""

from __future__ import annotations

import re

from app.ingestion.models import CorpusSpec, PageText, ParsedAct, ValidationResult

_ACT_TITLE_LINE_RE = re.compile(r"^(THE\s+.*?SANHITA,\s*\d{4})$", re.IGNORECASE)
_ACT_NUMBER_RE = re.compile(r"^NO\.\s*\d+\s+OF\s+\d{4}", re.IGNORECASE)


class SourceValidationError(ValueError):
    """Raised when the source does not match the expected corpus."""


def detect_act_title(pages: list[PageText]) -> str | None:
    """Find the printed act title + act number on the opening pages."""
    for page in pages[:3]:
        for i, line in enumerate(page.lines):
            match = _ACT_TITLE_LINE_RE.match(line)
            if match and i + 1 < len(page.lines) and _ACT_NUMBER_RE.match(page.lines[i + 1]):
                return re.sub(r"\s+", " ", line).title()
    return None


def validate_source(pages: list[PageText], spec: CorpusSpec, page_count: int) -> ValidationResult:
    """Validate the extracted source against the corpus spec (content-based)."""
    errors: list[str] = []
    warnings: list[str] = []

    title = detect_act_title(pages)
    if title is None:
        errors.append("no act title detected in source text")
    elif not re.search(spec.title_pattern, title, re.IGNORECASE):
        errors.append(f"source act title {title!r} does not match expected corpus {spec.act!r}")
    if page_count < spec.min_pages:
        errors.append(f"source has {page_count} pages, expected at least {spec.min_pages}")

    return ValidationResult(
        ok=not errors,
        act_title_detected=title,
        errors=errors,
        warnings=warnings,
    )


def validate_structure(act: ParsedAct, spec: CorpusSpec) -> ValidationResult:
    """Validate parsed statutory structure invariants."""
    errors: list[str] = []
    warnings: list[str] = []

    sections = act.sections
    if len(sections) < spec.min_sections:
        errors.append(f"parsed {len(sections)} sections, expected at least {spec.min_sections}")

    numbers = [s.number for s in sections]
    if numbers != sorted(set(numbers)):
        errors.append("section numbers are not strictly increasing / contain duplicates")
    if numbers and numbers[0] != 1:
        errors.append(f"first parsed section is {numbers[0]}, expected 1")
    if numbers and numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        errors.append("section numbers are not contiguous")

    for section in sections:
        if not section.text.strip():
            errors.append(f"section {section.number} has no text")
        if not section.title_confident and section.title is None:
            warnings.append(f"section {section.number}: title not associated")
        if section.blocks and section.blocks[0].kind.value != "body":
            warnings.append(f"section {section.number}: unexpected first block kind")

    return ValidationResult(
        ok=not errors,
        act_title_detected=act.act_title_detected,
        errors=errors,
        warnings=warnings,
    )
