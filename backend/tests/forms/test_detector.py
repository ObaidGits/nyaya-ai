"""Form detection tests (REQUIREMENTS B-007..B-010; ARCHITECTURE §25-§26)."""

from __future__ import annotations

from app.forms.detector import detect_forms, inspect_page
from app.ingestion.models import PageText


def _page(lines: list[str], index: int = 0) -> PageText:
    return PageText(index=index, printed_page=index + 1, lines=lines)


def test_form_start_with_title_detected() -> None:
    info = inspect_page(
        _page(["FORM No. 12", "WARRANT TO SEARCH", "(See section 96)", "To ......."])
    )
    assert info.is_form_start
    assert info.form_number == 12
    assert info.title == "WARRANT TO SEARCH"
    assert info.confidence == 1.0


def test_header_deeper_on_page_lowers_confidence() -> None:
    info = inspect_page(
        _page(
            [
                "SEC. 1] THE GAZETTE OF INDIA EXTRAORDINARY 249",
                "DIWAKAR SINGH,",
                "Joint Secretary to the Govt. of India.",
                "PUBLISHED BY THE CONTROLLER OF PUBLICATIONS",
                "",
                "FORM No. 58",
                "WARRANT OF IMPRISONMENT",
            ]
        )
    )
    assert info.is_form_start
    assert info.form_number == 58
    assert info.confidence < 1.0


def test_header_without_title_flags_review() -> None:
    info = inspect_page(_page(["FORM No. 9", ".......", "......"]))
    assert info.is_form_start
    assert info.title is None
    assert info.confidence < 1.0


def test_continuation_page_has_no_form_start() -> None:
    info = inspect_page(_page(["(8)On section 309(2).—That you, on or about the..."]))
    assert not info.is_form_start
    assert info.is_continuation


def test_empty_page_is_reported_not_invented() -> None:
    info = inspect_page(_page([]))
    assert not info.is_form_start
    assert not info.has_text
    assert info.confidence == 0.0


def test_grouping_multi_page_form() -> None:
    pages = [
        _page(["FORM No. 33", "WARRANT IN DUPLICATE", "body"], index=0),
        _page(["(8)On section 309(2).—continued body"], index=1),
        _page(["more continued body of the same form"], index=2),
        _page(["FORM No. 34", "SUMMONS TO WITNESS"], index=3),
    ]
    forms = detect_forms(pages)
    assert [(f.form_number, f.start_page_index, f.end_page_index) for f in forms] == [
        (33, 0, 2),
        (34, 3, 3),
    ]
    assert forms[0].title == "WARRANT IN DUPLICATE"
    assert forms[1].title == "SUMMONS TO WITNESS"


def test_duplicate_form_numbers_flagged_for_review() -> None:
    pages = [
        _page(["FORM No. 5", "FIRST TITLE"], index=0),
        _page(["FORM No. 5", "SECOND TITLE"], index=1),
    ]
    forms = detect_forms(pages)
    assert len(forms) == 2
    assert all(form.needs_review for form in forms)


def test_no_forms_detected_returns_empty() -> None:
    assert detect_forms([_page(["plain statute text, no forms here"])]) == []
