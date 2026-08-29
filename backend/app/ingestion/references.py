"""Cross-reference detection in statutory text (REQUIREMENTS A1-037/A1-038).

Detects internal references such as::

    section 2(11)
    section 103
    sections 81 to 84
    sub-section (1) of section 164

and returns them as normalized strings stored in each chunk's
``references`` array. Resolution at query time is explicitly deferred
(D-021 / A1-039 bonus).
"""

from __future__ import annotations

import re

# "section 103", "sections 383, 384 and 388", "section 2(11)"
_SECTIONS_RE = re.compile(
    r"\bsections?\s+(\d{1,3})(?:\s*\(\s*([0-9]{1,3})\s*\))?"
    r"((?:\s*(?:,|and|to|or)\s*\d{1,3}(?:\s*\(\s*[0-9]{1,3}\s*\))?)+)?",
    re.IGNORECASE,
)

# "sub-section (1) of section 164" -> attribute subsection to that section
_SUBSECTION_OF_RE = re.compile(
    r"sub-?\s*sections?\s*\(\s*([0-9]{1,3})\s*\)\s+of\s+section\s+(\d{1,3})",
    re.IGNORECASE,
)

_RANGE_RE = re.compile(r"\s+to\s+(\d{1,3})", re.IGNORECASE)


def _fmt(section: int, subsection: int | None) -> str:
    return f"section {section}({subsection})" if subsection else f"section {section}"


def detect_references(text: str) -> list[str]:
    """Extract normalized cross-references from statutory text.

    Returns a de-duplicated, order-preserved list. A "sections 81 to 84"
    range expands to each section in the range.
    """
    found: list[str] = []

    claimed_spans: list[tuple[int, int]] = []
    for match in _SUBSECTION_OF_RE.finditer(text):
        found.append(_fmt(int(match.group(2)), int(match.group(1))))
        claimed_spans.append(match.span())

    for match in _SECTIONS_RE.finditer(text):
        start, _ = match.span()
        if any(s <= start < e for s, e in claimed_spans):
            continue  # already captured as "section N(x)" above
        start = int(match.group(1))
        subsection = match.group(2)
        tail = match.group(3) or ""
        found.append(_fmt(start, int(subsection) if subsection else None))
        # Additional enumerated sections, e.g. ", 384 and 388" or " to 84".
        range_match = _RANGE_RE.search(tail)
        if range_match:
            end = int(range_match.group(1))
            if end > start and end - start <= 50:  # sanity bound on ranges
                found.extend(_fmt(n, None) for n in range(start + 1, end + 1))
        else:
            for num in re.findall(r"\d{1,3}", tail):
                found.append(_fmt(int(num), None))

    seen: set[str] = set()
    unique: list[str] = []
    for ref in found:
        if ref not in seen:
            seen.add(ref)
            unique.append(ref)
    return unique
