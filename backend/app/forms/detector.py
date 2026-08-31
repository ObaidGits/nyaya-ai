"""Form boundary detection and title scraping (ARCHITECTURE §25-§26).

Content-driven, never a hardcoded list of form titles (B-007/B-008):

* a page **starts** a form when a ``FORM No. <n>`` header appears near the top
  of the page text;
* a page without a form header is a **continuation** of the previous form;
* extracted titles are the printed headline lines following the form header
  (normalising the Gazette's broken intra-word spacing).

Confidence reflects parser certainty: a header in the top lines with a clean
title line scores 1.0; degenerate cases (header far down the page, empty
title, split/blank pages) lose confidence and raise ``needs_review``.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.ingestion.models import PageText

# Matches "FORM No. 12", "FORM No.12", "FORM  No.  3" — the Gazette prints
# both spaced and unspaced variants.
FORM_HEADER_RE = re.compile(r"^\s*FORM\s+No\.?\s*(\d+)\s*$", re.IGNORECASE)

# Numbered-charge lines like "(8)On section 309(2).—That you..." indicate a
# form's body continued from a previous page, not a new form.
_CONTINUATION_BODY_RE = re.compile(r"^\(\d+\)(On\s+section|That\s+you)", re.IGNORECASE)

# Gazette running heads/feet that must never be read as form titles.
_RUNNING_HEAD_RE = re.compile(
    r"GAZETTE\s+OF\s+INDIA|EXTRAORDINARY|UPLOADED\s+BY|PUBLISHED\s+BY"
    r"|^\(See\s+section",
    re.IGNORECASE,
)

# How many leading lines may hold the form header. Real form headers sit at
# or near the top of the page; deep headers are suspicious.
_HEADER_WINDOW = 6

_MIN_CONFIDENCE = 0.5


class PageFormInfo(BaseModel):
    """What the detector saw on one page."""

    index: int
    printed_page: int
    form_number: int | None = None
    title: str | None = None
    is_form_start: bool = False
    is_continuation: bool = False
    has_text: bool = True
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class DetectedForm(BaseModel):
    """A form group: start page through last continuation page."""

    form_number: int
    title: str
    start_page_index: int
    end_page_index: int
    confidence: float = Field(ge=0.0, le=1.0)
    needs_review: bool = False


def _clean_title(line: str) -> str:
    """Normalise Gazette spacing into a single printable title."""
    collapsed = re.sub(r"\s+", " ", line).strip()
    return collapsed


def _looks_like_title(line: str) -> bool:
    text = _clean_title(line)
    if not text or len(text) < 4:
        return False
    if FORM_HEADER_RE.match(line):
        return False
    if _RUNNING_HEAD_RE.search(text):
        return False
    # Titles are headline-style: mostly letters/spaces/hyphens, no fill-in dots.
    if "....." in text:
        return False
    letters = sum(c.isalpha() or c.isspace() for c in text)
    return letters / len(text) > 0.7


def inspect_page(page: PageText) -> PageFormInfo:
    """Classify one page (form start / continuation / empty)."""
    lines = [line for line in page.lines if line.strip()]
    info = PageFormInfo(index=page.index, printed_page=page.printed_page)
    if not lines:
        info.has_text = False
        info.confidence = 0.0
        return info

    header_number: int | None = None
    header_line = -1
    for position, line in enumerate(lines[:_HEADER_WINDOW]):
        match = FORM_HEADER_RE.match(line)
        if match:
            header_number = int(match.group(1))
            header_line = position
            break

    # Continuation body: numbered charge lines with no header above them.
    if header_number is None:
        info.is_continuation = any(
            _CONTINUATION_BODY_RE.match(line) for line in lines[:_HEADER_WINDOW]
        )
        return info

    info.is_form_start = True
    info.form_number = header_number
    info.confidence = 1.0 if header_line <= 1 else 0.8

    # Title: first title-looking line after the header (skipping the running
    # schedule head that precedes the first form of a page).
    title: str | None = None
    for line in lines[header_line + 1 : header_line + 4]:
        if _looks_like_title(line):
            title = _clean_title(line)
            break
    if title is None:
        # Header-only page (form number with title on a spot-checked layout):
        # keep going, flag for review.
        info.title = None
        info.confidence = min(info.confidence, 0.6)
    else:
        info.title = title
    return info


def detect_forms(pages: list[PageText]) -> list[DetectedForm]:
    """Group pages into forms (B-009/B-010).

    Starts a new form at each detected header; header-less pages continue
    the previous form. A header page with no preceding start (orphan) is
    still emitted — extraction failures surface honestly, never silently
    dropped.
    """
    infos = [inspect_page(page) for page in pages]
    forms: list[DetectedForm] = []

    current: DetectedForm | None = None
    for info in infos:
        if info.is_form_start and info.form_number is not None:
            confidence = info.confidence
            needs_review = confidence < _MIN_CONFIDENCE or info.title is None
            current = DetectedForm(
                form_number=info.form_number,
                title=info.title or f"Form {info.form_number}",
                start_page_index=info.index,
                end_page_index=info.index,
                confidence=confidence,
                needs_review=needs_review,
            )
            forms.append(current)
        elif current is not None:
            current.end_page_index = info.index
            if not info.has_text:
                current.needs_review = True
                current.confidence = min(current.confidence, 0.4)

    # Duplicate form numbers would break deterministic naming and manifest
    # uniqueness — flag both as untrusted.
    seen: dict[int, DetectedForm] = {}
    for form in forms:
        if form.form_number in seen:
            form.needs_review = True
            seen[form.form_number].needs_review = True
        else:
            seen[form.form_number] = form
    return forms
