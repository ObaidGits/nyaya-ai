"""End-to-end forms pipeline tests (REQUIREMENTS B-001..B-032)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.forms.models import MANIFEST_FILENAME
from app.forms.ocr import OcrFallback
from app.forms.pipeline import FormsExtractionError, FormsExtractor
from tests.forms.fixtures import FakeOcrEngine, make_forms_pdf

SOURCE_PAGES = [
    ["FORM No. 1", "NOTICE FOR APPEARANCE BY THE POLICE", "(See section 35)", "body"],
    ["FORM No. 2", "SUMMONS TO AN ACCUSED PERSON", "(See section 63)", "body"],
    ["(8)On section 309(2).—continuation of form 2"],
    ["still form 2 continuation"],
    ["FORM No. 3", "WARRANT OF ARREST", "(See section 72)", "body"],
]


def _write_source(tmp_path: Path, pages: list[list[str]] = SOURCE_PAGES) -> Path:
    source = tmp_path / "source.pdf"
    source.write_bytes(make_forms_pdf(pages))
    return source


def _extractor() -> FormsExtractor:
    return FormsExtractor(page_start=1, page_end=5, ocr=OcrFallback(FakeOcrEngine("")))


def test_pipeline_extracts_forms_with_manifest(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    output = tmp_path / "forms"
    manifest = _extractor().extract(str(source), output)

    assert [f.form_number for f in manifest.forms] == [1, 2, 3]
    form2 = manifest.forms[1]
    assert form2.title == "SUMMONS TO AN ACCUSED PERSON"
    assert form2.source_page_start == 2
    assert form2.source_page_end == 4
    assert form2.output_filename == "FORM-2_Summons-to-an-Accused-Person.pdf"
    import hashlib

    assert form2.sha256 == hashlib.sha256((output / form2.output_filename).read_bytes()).hexdigest()
    assert form2.byte_size == (output / form2.output_filename).stat().st_size


def test_manifest_written_with_required_fields(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    output = tmp_path / "forms"
    _extractor().extract(str(source), output)

    data = json.loads((output / MANIFEST_FILENAME).read_text())
    assert data["source"]["filename"] == "source.pdf"
    assert data["source"]["page_start"] == 1
    assert data["source"]["page_end"] == 5
    for form in data["forms"]:
        for key in (
            "form_number",
            "title",
            "source_page_start",
            "source_page_end",
            "output_filename",
            "byte_size",
            "sha256",
            "extraction_confidence",
            "needs_review",
        ):
            assert key in form


def test_multi_page_form_is_one_pdf(tmp_path: Path) -> None:
    from pypdf import PdfReader

    source = _write_source(tmp_path)
    output = tmp_path / "forms"
    manifest = _extractor().extract(str(source), output)

    form2 = manifest.forms[1]
    path = output / form2.output_filename
    assert path.is_file()
    reader = PdfReader(path)
    assert len(reader.pages) == 3  # pages 2-4 in one PDF (B-010)


def test_output_is_page_perfect_source_copy(tmp_path: Path) -> None:
    from pypdf import PdfReader

    source = _write_source(tmp_path)
    output = tmp_path / "forms"
    manifest = _extractor().extract(str(source), output)

    form3 = manifest.forms[2]
    reader = PdfReader(output / form3.output_filename)
    text = reader.pages[0].extract_text() or ""
    assert "WARRANT OF ARREST" in text.upper()
    assert "FORM No. 3" in text or "FORM  No. 3" in text


def test_extraction_is_idempotent_and_byte_identical(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    first_out, second_out = tmp_path / "run1", tmp_path / "run2"
    _extractor().extract(str(source), first_out)
    _extractor().extract(str(source), second_out)

    first_manifest = (first_out / MANIFEST_FILENAME).read_text()
    second_manifest = (second_out / MANIFEST_FILENAME).read_text()
    assert first_manifest == second_manifest
    for path in first_out.glob("FORM-*.pdf"):
        assert path.read_bytes() == (second_out / path.name).read_bytes()


def test_source_traceability_in_manifest(tmp_path: Path) -> None:
    import hashlib

    source = _write_source(tmp_path)
    manifest = _extractor().extract(str(source), tmp_path / "forms")
    assert manifest.source.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()


def test_ocr_repair_flows_into_extraction(tmp_path: Path) -> None:
    # Page 2 has no text layer: OCR (faked) recovers the form header.
    pages = [
        ["FORM No. 1", "NOTICE FOR APPEARANCE", "body"],
        [],  # scanned page -> OCR
        ["FORM No. 3", "WARRANT OF ARREST", "body"],
    ]
    source = tmp_path / "source.pdf"
    source.write_bytes(make_forms_pdf(pages))
    engine = FakeOcrEngine("FORM No. 2\nSUMMONS TO AN ACCUSED PERSON\n(See section 63)\n")
    extractor = FormsExtractor(page_start=1, page_end=3, ocr=OcrFallback(engine))
    manifest = extractor.extract(str(source), tmp_path / "forms")

    assert [f.form_number for f in manifest.forms] == [1, 2, 3]
    assert manifest.forms[1].title == "SUMMONS TO AN ACCUSED PERSON"


def test_missing_source_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(FormsExtractionError) as excinfo:
        _extractor().extract(str(tmp_path / "nope.pdf"), tmp_path / "out")
    assert excinfo.value.code == "FORMS_SOURCE_MISSING"


def test_no_forms_detected_fails_clearly(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(make_forms_pdf([["ordinary statute text with no forms"]]))
    with pytest.raises(FormsExtractionError) as excinfo:
        _extractor().extract(str(source), tmp_path / "out")
    assert excinfo.value.code == "FORMS_NONE_DETECTED"


def test_range_shorter_than_source_fails_clearly(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    extractor = FormsExtractor(page_start=50, page_end=60, ocr=OcrFallback(FakeOcrEngine("")))
    with pytest.raises(FormsExtractionError) as excinfo:
        extractor.extract(str(source), tmp_path / "out")
    assert excinfo.value.code == "FORMS_RANGE_INVALID"


def test_glued_form_header_number_survives_cleaning(tmp_path: Path) -> None:
    """Regression (2026-08-31): "FORM No.1" — number glued to the label —
    had its ".1" stripped by the generic glued-page-number chrome rule, so
    FORM No. 1 was silently dropped from the manifest (58 -> 57 forms on
    the real source). The pipeline normalizes the header before cleaning.
    """
    pages = [
        [
            "THE SECOND SCHEDULE",
            "(See section 522)",
            "FORM  No.1",
            "NOTICE FOR APPEARANCE BY THE POLICE",
            "body",
        ],
        ["FORM No. 2", "SUMMONS TO AN ACCUSED PERSON", "body"],
    ]
    source = _write_source(tmp_path, pages)
    manifest = _extractor().extract(str(source), tmp_path / "forms")

    assert [f.form_number for f in manifest.forms] == [1, 2]
    assert manifest.forms[0].title == "NOTICE FOR APPEARANCE BY THE POLICE"
