"""Source-PDF text cleanup (REQUIREMENTS A1-030..A1-035).

Removes Gazette-of-India running headers, footers, page-number artifacts,
GID/CG-DL markers and trailing underscore rules, and normalizes spacing
glitches introduced by the PDF text layer — without altering legal wording.

The cleanup rules were derived from inspection of the actual Gazette PDF
layout (249 pages, marginal notes at page tail, printed page numbers at text
start). They are layout-driven, not statute-specific, so they apply equally
to a replacement source PDF.
"""

from __future__ import annotations

import re

# Lines that are pure Gazette chrome and must never enter legal text.
_CHROME_LINE_RE = re.compile(
    r"""^(
        \d{1,3}$                                  |  # bare printed page number
        .*GAZETTE\s+OF\s+INDIA.*                  |  # running header (both sides)
        Sec\.\s*\d+\]                             |  # header fragment "Sec. 1]"
        \[Part\s+II.*                             |  # header fragment "[Part II—"
        _{5,}$                                    |  # underscore footer rules
        xxxGID.*                                  |  # GID markers
        .*CG-DL-E-.*                              |  # CG-DL download marker
        .*सी\.जी\.-डी\.एल\..*                     |  # Hindi CG-DL marker
        .*REGISTERED\s+NO.*                       |  # registration line
        MGIP.*                                    |  # press imprint
        UPLOADED\s+BY.*                           |  # signature-block chrome
        AND\s+PUBLISHED\s+BY.*                    |  # signature-block chrome
    )$""",
    re.VERBOSE | re.IGNORECASE,
)

# Spacing glitches seen in the Gazette text layer: "( 4)" / "( a)".
_OPEN_PAREN_SPACE_RE = re.compile(r"\(\s+([a-zA-Z0-9]{1,3})\)")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")

# Unicode dashes used in the Gazette instead of ASCII hyphens.
_DASHES_RE = re.compile("[–—]")  # noqa: RUF001 — literal Gazette characters


def clean_line(line: str) -> str:
    """Normalize one raw extracted line (spacing, dashes)."""
    cleaned = _OPEN_PAREN_SPACE_RE.sub(r"(\1)", line)
    cleaned = _DASHES_RE.sub("-", cleaned)
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned)
    return cleaned.strip()


def is_chrome_line(line: str) -> bool:
    """True if the line is a running header/footer/page artifact."""
    return bool(_CHROME_LINE_RE.match(line.strip()))


def join_wrapped_lines(lines: list[str]) -> list[str]:
    """Reconstruct words hyphen-broken across a line wrap (A1-034).

    A trailing hyphen followed by a continuation line starting with a
    lowercase letter is treated as a line wrap and merged into one line,
    keeping the hyphen. The hyphen is deliberately preserved: removing it
    would alter statutory wording ("Sub-registrar" -> "Subregistrar"), and
    legal-integrity rules forbid that. Documented heuristic in DECISIONS.md.
    """
    merged: list[str] = []
    for line in lines:
        if merged and merged[-1].endswith("-") and line[:1].islower() and not line.startswith("("):
            merged[-1] = merged[-1] + line
        else:
            merged.append(line)
    return merged


def clean_page_lines(raw_lines: list[str]) -> list[str]:
    """Clean one page: drop chrome lines, normalize, rejoin broken words."""
    cleaned = [clean_line(line) for line in raw_lines]
    body = [line for line in cleaned if line and not is_chrome_line(line)]
    return join_wrapped_lines(body)
