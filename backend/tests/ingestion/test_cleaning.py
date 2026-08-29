"""Cleanup tests (REQUIREMENTS A1-030..A1-034)."""

from app.ingestion.cleaning import clean_page_lines, is_chrome_line


def test_running_header_removed() -> None:
    assert is_chrome_line("Sec. 1] THE GAZETTE OF INDIA EXTRAORDINARY")
    assert is_chrome_line("6 THE GAZETTE OF INDIA EXTRAORDINARY [Part II—")


def test_page_number_and_footer_removed() -> None:
    assert is_chrome_line("31")
    assert is_chrome_line("__________________________________________________________")
    assert is_chrome_line("_____")


def test_gid_and_cgdl_markers_removed() -> None:
    assert is_chrome_line("xxxGIDHxxx")
    assert is_chrome_line("CG-DL-E-25122023-250884")


def test_signature_block_chrome_removed() -> None:
    assert is_chrome_line("MGIPMRND—532GI(S3)—25-12-2023.")
    assert is_chrome_line(
        "UPLOADED BY THE MANAGER, GOVERNMENT OF INDIA PRESS, MINTO ROAD, NEW DELHI–110002"  # noqa: RUF001
    )


def test_legal_text_is_not_chrome() -> None:
    assert not is_chrome_line("5. The State Government may appoint Magistrates.")


def test_paren_spacing_normalized() -> None:
    assert clean_page_lines(["sub-section ( 4) of section 9"]) == ["sub-section (4) of section 9"]
    assert clean_page_lines(["clause ( a) of the proviso"]) == ["clause (a) of the proviso"]


def test_unicode_dashes_normalized() -> None:
    assert clean_page_lines(["Explanation.—For the purposes"]) == ["Explanation.-For the purposes"]


def test_hyphenated_line_break_rejoined() -> None:
    # Gazette breaks compounds like "Sub-Registrar" across lines.
    assert clean_page_lines(["Sub-", "registrar shall"]) == ["Sub-registrar shall"]


def test_real_hyphen_word_kept_when_not_line_final() -> None:
    # "Sub-divisional Magistrate" is a genuine compound, not a wrap.
    assert clean_page_lines(["The Sub-divisional Magistrate shall"]) == [
        "The Sub-divisional Magistrate shall"
    ]


def test_blank_and_chrome_lines_dropped_from_page() -> None:
    lines = [
        "5",
        "",
        "9. (1) In every district there shall be Courts.",
        "Sec. 1] THE GAZETTE OF INDIA EXTRAORDINARY",
        "_____",
    ]
    assert clean_page_lines(lines) == ["9. (1) In every district there shall be Courts."]
