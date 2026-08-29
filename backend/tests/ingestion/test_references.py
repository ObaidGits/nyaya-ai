"""Cross-reference detection tests (REQUIREMENTS A1-037/A1-038)."""

from app.ingestion.references import detect_references


def test_plain_section_reference() -> None:
    assert detect_references("as provided in section 103") == ["section 103"]


def test_subsection_reference() -> None:
    assert detect_references("under section 2(11) of this Sanhita") == ["section 2(11)"]


def test_enumerated_sections() -> None:
    assert detect_references("except as provided in sections 383, 384 and 388") == [
        "section 383",
        "section 384",
        "section 388",
    ]


def test_range_expansion() -> None:
    assert detect_references("punishable under sections 81 to 84 (both inclusive)") == [
        "section 81",
        "section 82",
        "section 83",
        "section 84",
    ]


def test_subsection_of_section_pattern() -> None:
    assert detect_references("the order under sub-section (1) of section 164 considers") == [
        "section 164(1)"
    ]


def test_no_false_positive_from_plain_numbers() -> None:
    assert detect_references("within a period of fourteen days") == []


def test_deduplicated_and_order_preserved() -> None:
    text = "section 5 applies; section 5 also overrides section 2(11)."
    assert detect_references(text) == ["section 5", "section 2(11)"]
