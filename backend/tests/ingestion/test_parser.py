"""Structure parser tests (REQUIREMENTS A1-001, A1-030..A1-036)."""

import pytest
from app.ingestion.models import BlockKind, CorpusSpec, PageText
from app.ingestion.parser import StructureParser
from tests.ingestion.fixtures import FIXTURE_SPEC, make_pages


def _parse(pages: list[PageText], spec: CorpusSpec = FIXTURE_SPEC):
    return StructureParser(spec).parse(pages)


def test_act_title_detected_from_content() -> None:
    act = _parse(make_pages())
    assert act.act_title_detected == "The Test Sanhita, 2023"


def test_chapters_parsed_with_titles() -> None:
    act = _parse(make_pages())
    chapters = {(s.chapter_number, s.chapter_title) for s in act.sections}
    assert ("I", "PRELIMINARY") in chapters
    assert ("II", "OFFENCES") in chapters


def test_sections_parsed_in_order() -> None:
    act = _parse(make_pages())
    assert [s.number for s in act.sections] == [1, 2, 3, 4]


def test_section_number_associated_with_title_via_marginal_notes() -> None:
    act = _parse(make_pages())
    by_number = {s.number: s for s in act.sections}
    assert by_number[1].title == "Short title"
    assert by_number[2].title == "Definitions"
    assert by_number[3].title == "Test offence"
    # Section 4's note is on page 2 (its own page).
    assert by_number[4].title == "Long section"
    assert by_number[1].title_confident is True


def test_marginal_notes_not_contaminating_section_text() -> None:
    act = _parse(make_pages())
    for section in act.sections:
        assert "GAZETTE" not in section.text
        assert "GID" not in section.text
        assert "Short title" not in section.text or section.number == 1


def test_subsections_and_clauses_tagged() -> None:
    act = _parse(make_pages())
    section5 = next(s for s in act.sections if s.number == 4)
    subs = [b.subsection for b in section5.blocks if b.kind == BlockKind.BODY]
    assert "(1)" in subs and "(11)" in subs
    clauses = [b.clause for b in section5.blocks if b.clause is not None]
    assert clauses == ["(a)", "(b)"]


def test_proviso_exception_explanation_illustration_detected() -> None:
    act = _parse(make_pages())
    section3 = next(s for s in act.sections if s.number == 3)
    kinds = {b.kind for b in section3.blocks}
    assert BlockKind.PROVISO in kinds
    assert BlockKind.EXCEPTION in kinds
    assert BlockKind.EXPLANATION in kinds
    assert BlockKind.ILLUSTRATION in kinds
    assert section3.has_proviso and section3.has_exception
    assert section3.has_explanation and section3.has_illustration


def test_proviso_text_preserved_verbatim() -> None:
    act = _parse(make_pages())
    section3 = next(s for s in act.sections if s.number == 3)
    proviso = next(b for b in section3.blocks if b.kind == BlockKind.PROVISO)
    assert proviso.text.startswith("Provided that nothing in this section applies")


def test_page_span_recorded_for_multiline_section() -> None:
    act = _parse(make_pages())
    section5 = next(s for s in act.sections if s.number == 4)
    assert section5.page_start == 2
    assert section5.page_end == 2
    section1 = next(s for s in act.sections if s.number == 1)
    assert section1.page_start == 1


def test_statute_ends_before_signature_block() -> None:
    pages = make_pages()
    pages.append(
        PageText(
            index=2,
            printed_page=3,
            lines=[
                "3",
                "MGIPMRND-532GI(S3)-25-12-2023.",
                "DIWAKAR SINGH,",
                "Joint Secretary & Legislative Counsel to the Govt. of India.",
                "FORM No. 1",
                "NOTICE FOR APPEARANCE",
                "1. This line must not become a section.",
            ],
        )
    )
    act = _parse(pages)
    assert [s.number for s in act.sections] == [1, 2, 3, 4]
    assert all("NOTICE" not in s.text for s in act.sections)


def test_section_continuing_across_page_boundary() -> None:
    pages = make_pages()
    # Split section 4's text across two pages.
    p2 = pages[1]
    mid = len(p2.lines) // 2
    tail = [ln for ln in p2.lines[mid:] if not ln.startswith("_")]
    pages[1] = PageText(index=1, printed_page=2, lines=p2.lines[:mid])
    pages.append(PageText(index=2, printed_page=3, lines=["3", *tail]))
    act = _parse(pages)
    section5 = next(s for s in act.sections if s.number == 4)
    assert section5.page_start == 2
    assert section5.page_end == 3


def test_missing_act_title_raises() -> None:
    pages = [PageText(index=0, printed_page=1, lines=["1. Some text."])]
    with pytest.raises(ValueError, match="act title not found"):
        _parse(pages)


def test_uncertain_note_association_flagged() -> None:
    pages = make_pages()
    # Two note clusters but only one new section on page 1: association
    # must be flagged rather than silently guessed.
    pages[0] = PageText(
        index=0,
        printed_page=1,
        lines=[
            "1",
            "THE  TEST  SANHITA,  2023",
            "NO. 99 OF 2023",
            "1. First section text here that is long enough to be content.",
            "Alpha.",
            "Beta.",
        ],
    )
    parser = StructureParser(FIXTURE_SPEC)
    act = parser.parse(pages)
    assert parser.warnings  # uncertainty surfaced, not hidden
    assert act.sections[0].title_confident is False
