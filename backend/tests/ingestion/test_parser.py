"""Structure parser tests (REQUIREMENTS A1-001, A1-030..A1-036)."""

import pytest
from app.ingestion.cleaning import clean_page_lines
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


# -- marginal-note recovery in the flat text layer (BNS Gazette QA) ------


def _pages(lines_by_page: list[list[str]]) -> list[PageText]:
    """Two-section pages from raw lines (act title on page 1)."""
    pages = [
        PageText(
            index=0,
            printed_page=1,
            lines=clean_page_lines(
                ["1", "THE  TEST  SANHITA,  2023", "NO. 99 OF 2023", *lines_by_page[0]]
            ),
        )
    ]
    for i, lines in enumerate(lines_by_page[1:], start=2):
        pages.append(
            PageText(index=i - 1, printed_page=i, lines=clean_page_lines([str(i), *lines]))
        )
    return pages


def test_period_glued_note_fragment_recovered() -> None:
    # F1: "...liable to fine.Husband or" — the note is glued after a period.
    act = _parse(
        _pages(
            [
                [
                    "1. Whoever commits test offence shall be punished and shall be liable to fine.",
                ],
                [
                    "2. Whoever commits another test offence shall be punished with imprisonment",
                    "for a term and shall also be liable to fine.Husband or",
                    "relative of husband.",
                    "3. Body of the third section continues in this manner.",
                ],
            ]
        )
    )
    by_number = {s.number: s for s in act.sections}
    # The cluster flushes at section 3's header: the note precedes that
    # header, so it is section 3's marginal note (positional default).
    assert by_number[3].title == "Husband or relative of husband"
    assert by_number[3].title_confident is False  # reconstructed from a fragment
    assert "Husband" not in by_number[2].text  # recovered out of the body


def test_space_glued_note_fragment_with_comma_recovered() -> None:
    # F2: a capitalized fragment glued after a space, ending on a comma.
    act = _parse(
        _pages(
            [
                [
                    "1. Whoever hires, employs or engages any child to commit an offence shall be Hiring,",
                    "punished with imprisonment for a term.",
                ],
                ["2. Body of the second section continues in this manner."],
            ]
        )
    )
    by_number = {s.number: s for s in act.sections}
    # The fragment precedes section 2's header (it was glued to the END of
    # section 1's body line): a precede-position note for section 2.
    assert by_number[2].title == "Hiring"
    assert by_number[2].title_confident is False  # reconstructed from a fragment
    assert "Hiring" not in by_number[1].text  # recovered out of the body
    # The lowercase body continuation after the glue is NOT note text.
    assert "punished with imprisonment" in by_number[1].text


def test_function_word_fragment_recovered_while_cluster_open() -> None:
    # F4: body line ends "...shall not" + glued note word "for"; the note
    # cluster is already open (header glued fragment "Punishment").
    act = _parse(
        _pages(
            [
                [
                    "1. Whoever commits a first offence shall be punished with imprisonment.",
                ],
                [
                    "2. Whoever commits a second offence shall be punished with rigorous imprisonment",
                    "Punishment",
                    "of either description for a term which shall not for",
                    "rape.",
                    "3. Whoever commits rape shall be punished as provided herein.",
                ],
            ]
        )
    )
    by_number = {s.number: s for s in act.sections}
    # The completed cluster flushes at section 3's header (precede note).
    assert by_number[3].title == "Punishment for rape"
    assert by_number[3].title_confident is False  # reconstructed
    assert "for rape" not in by_number[2].text  # recovered out of the body


def test_interleaved_note_words_reconstructed() -> None:
    # The margin column prints word-by-word between body lines.
    act = _parse(
        _pages(
            [
                [
                    "1. Whoever, having the care of a child under twelve years of age, leaves the child",
                    "Exposure and",
                    "in any place with the intention of wholly abandoning the child, shall be punished",
                    "abandonment.",
                    "with imprisonment of either description for a term which may extend to seven years.",
                ],
                ["2. The second section body follows here in this manner."],
            ]
        )
    )
    by_number = {s.number: s for s in act.sections}
    assert by_number[1].title == "Exposure and abandonment"
    assert by_number[1].title_confident is False  # interleaved reconstruction
    assert "Exposure and abandonment" not in by_number[1].text


def test_statute_prose_wrap_word_not_a_note() -> None:
    # "...is guilty under this / section" — a wrapped cross-reference word.
    act = _parse(
        _pages(
            [
                [
                    "1. Whoever commits a test offence is guilty under this",
                    "section",
                ],
                ["2. Whoever commits another offence shall be punished accordingly."],
            ]
        )
    )
    by_number = {s.number: s for s in act.sections}
    assert by_number[1].title is None  # no junk cluster from the wrap word
    assert "section" in by_number[1].text


def test_body_wrap_before_header_not_a_note() -> None:
    # "...A has therefore abetted / murder" before the next section header —
    # lowercase continuation is body text, unlike a TitleCase precede note.
    # A lowercase sentence tail longer than 4 words is body too, and must
    # not swallow the carried precede note into the body.
    act = _parse(
        _pages(
            [
                [
                    "1. A, by instigation, voluntarily causes Z to commit suicide. A has therefore abetted",
                    "murder",
                ],
                [
                    "Causing",
                    "2. Whoever causes harm by doing an act with the intention of causing harm",
                    "with imprisonment of either description for a term which may",
                    "extend to ten years, and shall also be liable to fine.",
                ],
            ]
        )
    )
    by_number = {s.number: s for s in act.sections}
    assert by_number[1].title is None
    assert "murder" in by_number[1].text  # body wrap, not a note
    assert by_number[2].title == "Causing"  # precede note for section 2
    assert by_number[2].title_confident is False
    assert "liable to fine" in by_number[2].text  # body, not note


def test_apostrophe_tail_line_not_a_note() -> None:
    act = _parse(
        _pages(
            [
                [
                    "1. In this Sanhita offenders are'",
                    "liable under the provisions hereof.",
                ],
                ["2. The second section body follows here in this manner."],
            ]
        )
    )
    by_number = {s.number: s for s in act.sections}
    assert by_number[1].title is None


def test_lone_proper_noun_terminates_note_without_text() -> None:
    # "India." closes the note run but contributes no title text.
    act = _parse(
        _pages(
            [
                [
                    "1. This Act applies to offences committed within the territory.",
                    "Short title,",
                    "commencement.",
                    "Every person is liable for acts of which he is guilty within",
                    "India.",
                ],
                ["2. The second section body follows here in this manner."],
            ]
        )
    )
    by_number = {s.number: s for s in act.sections}
    assert by_number[1].title == "Short title, commencement"


def test_etc_only_cluster_dropped() -> None:
    act = _parse(
        _pages(
            [
                [
                    "1. Whoever commits a test offence shall be punished with imprisonment and fine.",
                    "etc.",
                ],
                ["2. The second section body follows here in this manner."],
            ]
        )
    )
    by_number = {s.number: s for s in act.sections}
    assert by_number[1].title is None


def test_group_heading_with_etc_skipped() -> None:
    act = _parse(
        _pages(
            [
                [
                    "1. Whoever commits a test offence shall be punished with imprisonment and fine.",
                ],
                [
                    "Of causing harm, etc.",
                    "2. Whoever causes harm shall be punished accordingly.",
                ],
            ]
        )
    )
    by_number = {s.number: s for s in act.sections}
    assert by_number[2].title is None  # the group heading is not a title


def test_body_tail_starter_not_a_note() -> None:
    # "...exceeds / than five lakh rupees" — a body sentence tail.
    act = _parse(
        _pages(
            [
                [
                    "1. Where the loss caused exceeds",
                    "than five lakh rupees",
                    "the offender shall be punished with imprisonment.",
                ],
                ["2. The second section body follows here in this manner."],
            ]
        )
    )
    by_number = {s.number: s for s in act.sections}
    assert by_number[1].title is None


def test_page_split_note_carried_across_boundary() -> None:
    # "Amount of" at the page tail completes as "fine, etc." on the next page.
    act = _parse(
        _pages(
            [
                [
                    "1. Whoever commits a test offence shall be punished with fine as provided.",
                ],
                [
                    "Amount of",
                    "fine, etc.",
                    "2. The amount of fine imposed shall not exceed the limit.",
                ],
            ]
        )
    )
    by_number = {s.number: s for s in act.sections}
    # The cluster flushes at section 2's header (precede note), and the
    # content match confirms section 2 over the "fine" in section 1.
    assert by_number[2].title == "Amount of fine, etc"


def test_stale_untitled_section_does_not_steal_cluster() -> None:
    # s.1 stays untitled (its note was lost); the page-2 note must attach to
    # the flush-adjacent s.2, not to the stale s.1.
    act = _parse(
        _pages(
            [
                [
                    "1. Whoever commits a nameless offence shall be punished with imprisonment.",
                ],
                [
                    "Second offence.",
                    "2. Whoever commits the second offence shall be punished accordingly.",
                ],
            ]
        )
    )
    by_number = {s.number: s for s in act.sections}
    assert by_number[1].title is None
    assert by_number[2].title == "Second offence"


def test_lone_connective_cluster_dropped() -> None:
    # "under" stranded by the flat text layer is a fragment, never a title.
    act = _parse(
        _pages(
            [
                [
                    "1. Whoever commits a test offence shall be punished with imprisonment.",
                    "under",
                ],
                [
                    "2. Whoever commits another offence shall be punished accordingly.",
                    "Under cover.",
                ],
            ]
        )
    )
    by_number = {s.number: s for s in act.sections}
    assert by_number[1].title is None  # lone connective dropped, not junk-titled
    # Multi-word note starting with "Under" is still a title.
    assert by_number[2].title == "Under cover"


def test_carry_does_not_merge_across_body_text() -> None:
    # A carried fragment followed by 2+ body lines is its own note, not a
    # prefix of the NEXT section's cluster ("Voyeurism" / "Stalking.").
    act = _parse(
        _pages(
            [
                [
                    "1. Whoever watches a woman engaging in a private act in circumstances",
                    "Voyeurism",
                    "where she would usually expect not to be observed shall be punished",
                    "on first conviction, and shall also be liable to fine.",
                    "Explanation.'A consent given under coercion is not consent here.",
                ],
                [
                    "Stalking.",
                    "2. Whoever follows a woman repeatedly shall be punished accordingly",
                    "with imprisonment for a term.",
                ],
            ]
        )
    )
    by_number = {s.number: s for s in act.sections}
    assert by_number[1].title == "Voyeurism"
    assert by_number[2].title == "Stalking"


def test_lowercase_margin_continuation_without_period_is_a_note() -> None:
    # "Refusing to / answer public / servant" — margin column wraps have
    # no sentence punctuation and stay note text.
    act = _parse(
        _pages(
            [
                [
                    "1. Whoever refuses to answer a question shall be punished with fine",
                    "or with both.Refusing to",
                    "answer public",
                    "servant",
                    "authorised to",
                    "question.",
                ],
                ["2. The second section body follows here in this manner."],
            ]
        )
    )
    by_number = {s.number: s for s in act.sections}
    assert by_number[1].title == "Refusing to answer public servant authorised to question"
