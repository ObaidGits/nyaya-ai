"""Shared synthetic Gazette-style fixtures for ingestion tests.

The fixtures mimic the observed Gazette layout (act title + "NO. x OF 2023"
on page 1, printed page number first, running headers/footers, marginal
notes as a short-line run at page tail) using a fictional act so tests do
not depend on any real source PDF.
"""

from __future__ import annotations

from app.ingestion.cleaning import clean_page_lines
from app.ingestion.models import CorpusSpec, PageText


def _page(index: int, printed: int, raw_lines: list[str]) -> PageText:
    """Build a cleaned PageText from raw Gazette-style lines (chrome included
    so the cleaning layer is genuinely exercised by every fixture user)."""
    return PageText(
        index=index,
        printed_page=printed,
        lines=clean_page_lines(raw_lines),
    )


FIXTURE_SPEC = CorpusSpec(
    act="Test Sanhita, 2023",
    act_short="TS",
    title_pattern=r"the\s+test\s+sanhita",
    min_sections=2,
    min_pages=1,
)


def make_pages() -> list[PageText]:
    """Two Gazette-style pages covering: short section, long section with
    subsections/clauses/proviso/exception/explanation/illustration,
    cross-references, hyphenation, chrome lines, page boundary."""
    page1 = _page(
        0,
        1,
        [
            "1",
            "THE  TEST  SANHITA,  2023",
            "NO. 99 OF 2023",
            "An Act to test parsing.",
            "CHAPTER I",
            "PRELIMINARY",
            "1. (1) This Act may be called the Test Sanhita, 2023.",
            "2. Short definitions apply as provided in section 4 and section 2(11).",
            "CHAPTER II",
            "OFFENCES",
            "3. Whoever tests anything shall be punished according to section 4.",
            "Provided that nothing in this section applies to exempt testing.",
            "Exception.—Testing done under legal authority is not an offence.",
            "Explanation.—For the purposes of this section, testing includes checks.",
            "Illustration: A runs a check under legal authority. This is not an offence.",
            "xxxGIDHxxx",
            "Sec. 1] THE GAZETTE OF INDIA EXTRAORDINARY",
            "Short",
            "title.",
            "Definitions.",
            "Test",
            "offence.",
        ],
    )
    long_body: list[str] = []
    for i in range(1, 12):
        long_body.append(
            f"({i}) The detailed provisions of this subsection number {i} are "
            "set out at length in this paragraph covering various contingencies "
            "and requirements that occupy multiple lines of statutory text."
        )
    page2_lines = [
        "2",
        "Sec. 1] THE GAZETTE OF INDIA EXTRAORDINARY",
        "4. The long section begins here and continues over subsections.",
        *long_body[:6],
        "Provided that subsection (1) of section 164 is complied with first.",
        "(a) each clause shall be complied with;",
        "(b) references to sections 81 to 84 include both endpoints;",
        *long_body[6:],
        "_____",
        "__________________________________________________________",
        "Long",
        "section.",
    ]
    page2 = _page(1, 2, page2_lines)
    return [page1, page2]
