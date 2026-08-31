"""Plan 9.1 unit-test gap coverage: slugifier punctuation (T-004) and
forms title extraction (T-005)."""

from __future__ import annotations

from app.forms.naming import form_filename, slugify_title
from app.ingestion.models import PageText


class TestSlugifierPunctuation:
    def test_punctuation_only_words_are_dropped(self) -> None:
        assert slugify_title("WARRANT (OF ARREST) — 2023!") == "Warrant-of-Arrest-2023"

    def test_unsafe_chars_become_single_dashes(self) -> None:
        assert slugify_title("BAIL//BOND??BOND") == "Bail-Bond-Bond"

    def test_leading_and_trailing_dashes_stripped(self) -> None:
        assert slugify_title("-- WARRANT --") == "Warrant"

    def test_connectives_stay_lowercase(self) -> None:
        assert slugify_title("BOND AND BAIL-BOND AFTER A WARRANT") == (
            "Bond-and-Bail-Bond-After-a-Warrant"
        )

    def test_filename_embeds_number_and_slug(self) -> None:
        assert form_filename(35, "WARRANT OF ARREST") == "FORM-35_Warrant-of-Arrest.pdf"


class TestFormsTitleExtraction:
    def _page(self, index: int, lines: list[str]) -> PageText:
        return PageText(index=index, printed_page=index + 1, lines=lines)

    def test_headline_line_is_extracted_as_title(self) -> None:
        from app.forms.detector import detect_forms, inspect_page

        page = self._page(
            231,
            [
                "FORM No. 35",
                "WARRANT OF ARREST",
                "To the officer in charge of the police station",
                "WHEREAS complaint has been made...",
            ],
        )
        info = inspect_page(page)
        assert info.is_form_start
        assert info.form_number == 35
        assert info.title == "WARRANT OF ARREST"
        forms = detect_forms([page])
        assert forms and forms[0].title == "WARRANT OF ARREST"

    def test_running_heads_are_never_titles(self) -> None:
        from app.forms.detector import inspect_page

        page = self._page(
            240,
            [
                "THE GAZETTE OF INDIA EXTRAORDINARY",
                "FORM No. 45",
                "NOTICE TO PRODUCE DOCUMENT",
                "You are hereby called upon to produce...",
            ],
        )
        info = inspect_page(page)
        assert info.is_form_start
        assert info.title == "NOTICE TO PRODUCE DOCUMENT"

    def test_header_only_page_flags_needs_review(self) -> None:
        from app.forms.detector import detect_forms, inspect_page

        page = self._page(244, ["FORM No. 50", ".....................", "NAME: ______ AGE: ____"])
        info = inspect_page(page)
        assert info.form_number == 50
        assert info.title is None
        forms = detect_forms([page])
        assert forms and forms[0].needs_review

    def test_blank_page_yields_no_form(self) -> None:
        from app.forms.detector import inspect_page

        info = inspect_page(self._page(250, ["", "  "]))
        assert not info.has_text
        assert not info.is_form_start
        assert info.title is None
