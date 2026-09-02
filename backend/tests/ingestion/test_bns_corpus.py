"""Corpus validation against the official BNS Gazette PDF (P0 gate).

The Gazette's flat text layer interleaves marginal notes (section titles)
with the statute body in several modes; the parser reconstructs them.
This module pins the reconstruction quality on the ACTUAL corpus:

* 358 sections, numbered consecutively;
* every parsed title graded against an independently sourced reference
  list (devgan.in, 358 titles) — EXACT / NEAR (ratio >= 0.85) /
  PARTIAL (token subset) / WRONG / MISSING;
* no KNOWN-WRONG title may ship silently: a WRONG or MISSING title must
  carry title_confident=False (an honest review flag, never false
  certainty);
* known junk fragments ("restrain that person.", "commit an such person
  himself.") must never become titles;
* the marginal-note words must not leak into section body text.

Skipped automatically when the Gazette PDF is not present.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

import pytest
from app.ingestion.extract import PypdfPageExtractor
from app.ingestion.models import CorpusSpec
from app.ingestion.parser import StructureParser

BNS_PDF = Path(__file__).resolve().parents[3] / "data" / "raw" / "BNS_gazette_2023.pdf"
GOLDEN_TITLES = Path(__file__).parent / "golden" / "bns_titles.txt"

pytestmark = pytest.mark.skipif(not BNS_PDF.exists(), reason="BNS Gazette PDF not present")


def _norm(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(s.split())


@pytest.fixture(scope="module")
def act() -> object:
    pages = PypdfPageExtractor().extract(str(BNS_PDF))
    return StructureParser(CorpusSpec.bns()).parse(pages)


def _load_reference() -> dict[int, str]:
    ref: dict[int, str] = {}
    for line in GOLDEN_TITLES.read_text(encoding="utf-8").splitlines():
        n, _, title = line.partition("|")
        ref[int(n)] = title.strip()
    return ref


@pytest.fixture(scope="module")
def grades(act: object) -> dict[str, list[int]]:
    ref = _load_reference()
    out: dict[str, list[int]] = {
        "exact": [],
        "near": [],
        "partial": [],
        "wrong": [],
        "missing": [],
    }
    for section in act.sections:  # type: ignore[attr-defined]
        want = ref.get(section.number, "")
        got = section.title or ""
        if not got:
            out["missing"].append(section.number)
            continue
        if _norm(got) == _norm(want):
            out["exact"].append(section.number)
            continue
        ratio = difflib.SequenceMatcher(None, _norm(got), _norm(want)).ratio()
        if ratio >= 0.85:
            out["near"].append(section.number)
            continue
        gt, wt = set(_norm(got).split()), set(_norm(want).split())
        if gt and (gt <= wt or wt <= gt):
            out["partial"].append(section.number)
        else:
            out["wrong"].append(section.number)
    return out


def test_section_count_and_consecutive_numbering(act: object) -> None:
    sections = act.sections  # type: ignore[attr-defined]
    assert len(sections) == 358
    assert [s.number for s in sections] == list(range(1, 359))


def test_no_known_wrong_title_is_asserted_confidently(
    act: object, grades: dict[str, list[int]]
) -> None:
    """A WRONG/MISSING title is acceptable only behind an honest review
    flag (title_confident=False). False certainty is worse than a flag."""
    by_number = {s.number: s for s in act.sections}  # type: ignore[attr-defined]
    bad = grades["wrong"] + grades["missing"]
    offenders = [n for n in bad if by_number[n].title_confident]
    assert not offenders, f"confidently wrong titles: {offenders}"


def test_title_quality_floor(act: object, grades: dict[str, list[int]]) -> None:
    """P0 floor: the large majority of titles exact, none asserted wrong,
    MISSING honestly flagged rather than junk-filled."""
    assert len(grades["exact"]) >= 250, f"exact titles {len(grades['exact'])} < 250"
    assert len(grades["wrong"]) <= 12, f"wrong titles {grades['wrong']}"
    assert len(grades["missing"]) <= 15, f"missing titles {grades['missing']}"
    # Junk body fragments must never be ASSERTED as titles (P0 corruption
    # class): if the flat layer mis-captures one, it must carry the review
    # flag, never false certainty.
    junk = ("restrain that person", "commit an such person himself", "hurt.")
    junk_norms = {_norm(j) for j in junk}
    by_number = {s.number: s for s in act.sections}  # type: ignore[attr-defined]
    for n, section in by_number.items():
        if section.title and _norm(section.title) in junk_norms:
            assert not section.title_confident, (
                f"s.{n} titled with body fragment {section.title!r} and asserted"
            )


def test_marginal_notes_do_not_leak_into_section_text(act: object) -> None:
    """The P0 audit issue: marginal-note words corrupting the statute body
    (body integrity, 72/425 fragments at audit time). Spot-gate the exact
    regression shapes: note-only phrases must not appear in body text."""
    by_number = {s.number: s for s in act.sections}  # type: ignore[attr-defined]
    # s.114's body ends "...is said to cause hurt." — the flat layer once
    # stole the note "Hurt." and the body tail with it.
    assert "is said to cause hurt" in by_number[114].text
    # s.95's body ends "...as if the offence has been committed by such
    # person himself." — the note "Hiring, employing or engaging a child
    # to commit an offence." interleaves word-wise through this sentence.
    assert "committed by such person himself" in by_number[95].text
