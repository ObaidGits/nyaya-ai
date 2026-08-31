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

# Chrome glued INLINE into body/note lines by the text layer (QA 2026-08-31):
#   "…with both.Sec. 1] THE…"  → running header glued after a period
#   "…done by him alone.20 THE" → page number + header start glued at line end
#   "…application.2 THE"        → same, glued into a marginal-note line
# Each pattern is anchored to the line end (or a "Sec. N]" marker) so statutory
# text can never be clipped: a section body never ends in "N THE" or "Sec. N]".
_INLINE_CHROME_RES = (
    re.compile(r"\.?Sec\.\s*\d+\].*$"),  # "…fine.Sec. 1] THE GAZETTE…"
    re.compile(r"\d{1,3}\s+THE\s+GAZETTE.*$"),  # "…alone.20 THE GAZETTE…"
    re.compile(r"\d{1,3}\s+THE$"),  # "…application.2 THE" (header fragment tail)
    re.compile(r"\.\d{1,3}$"),  # "…begging.46" (page number glued to line end)
)

# Whole-line chrome fragments left over when the header splits across lines
# (e.g. "Sec. 1]" on one line, "THE" on the next).
_CHROME_LINE_RE = re.compile(
    r"""^(
        \d{1,3}$                                  |  # bare printed page number
        \d{1,3}\]$                                |  # header fragment "1]"
        THE$                                      |  # header fragment "THE"
        Sec\.\s*\d+\]                             |  # header fragment "Sec. 1]"
        \[Part\s+II.*                             |  # header fragment "[Part II—"
        .*GAZETTE\s+OF\s+INDIA.*                  |  # running header (both sides)
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

# Marginal-note glue: some Gazette layouts emit the left margin note and the
# section header on one text line ("punishment.6. In calculating ..."). The
# split fires only when a lowercase/paren tail is immediately followed by a
# bare "N. " section-header start (capital letter or subsection marker) —
# statutory cross-references ("section 474 of ...") never match because they
# lack the trailing period+space before the number.
_MARGINAL_GLUE_RE = re.compile(
    # "…India.152. Whoever …" (dot-glued) or "Malignant act272. Whoever …"
    # (glued without the period). Marginal notes end lowercase; years in
    # prose are four digits and cannot satisfy the 1-3 digit header.
    r"^(?P<margin>[A-Za-z][^.\d]*[a-z])\.?(?P<header>\d{1,3}\. (?=[A-Z(]).*)$"
)


def split_marginal_glue(lines: list[str]) -> list[str]:
    """Split section headers glued to a trailing marginal-note fragment.

    Purely positional (layout artifact repair) — no wording is added,
    removed, or reordered; the marginal fragment keeps its own line so the
    parser can classify both halves correctly.
    """
    result: list[str] = []
    for line in lines:
        match = _MARGINAL_GLUE_RE.match(line)
        if match:
            result.append(match.group("margin") + ".")
            result.append(match.group("header"))
        else:
            result.append(line)
    return result


def clean_line(line: str) -> str:
    """Normalize one raw extracted line (spacing, dashes, inline chrome)."""
    cleaned = _OPEN_PAREN_SPACE_RE.sub(r"(\1)", line)
    cleaned = _DASHES_RE.sub("-", cleaned)
    # The Gazette text layer renders curly quotes as middle dots ("·rape·").
    cleaned = cleaned.replace("·", "'")
    for pattern in _INLINE_CHROME_RES:
        cleaned = pattern.sub("", cleaned)
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
    return join_wrapped_lines(split_marginal_glue(body))
