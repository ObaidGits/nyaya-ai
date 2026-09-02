"""OCR fallback tests (REQUIREMENTS B-026..B-029; ARCHITECTURE §29).

The fake-engine tests below pin the decision logic (when OCR engages, when it
must not, how failures degrade). The final test runs the REAL tesseract +
pdftoppm toolchain over a controlled fixture: a PDF whose middle page is
image-only (no text layer) — the exact condition the fallback exists for —
flanked by clean text pages. It is skipped when the binaries (or pymupdf)
are unavailable; live data never needed OCR (pp.190-249 all carry text
layers), so this fixture is the only controlled proof of the path.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from app.forms.ocr import OcrFallback, TesseractOcr, normalize_ocr_text, text_is_usable
from app.ingestion.models import PageText
from tests.forms.fixtures import make_scanned_forms_pdf


def test_usable_text_heuristics() -> None:
    assert text_is_usable("WARRANT OF ARREST To the officer in charge")
    assert not text_is_usable("")
    assert not text_is_usable("....")
    assert not text_is_usable("ab")  # too short
    assert not text_is_usable("•\x00•\x00•\x00•\x00•\x00•\x00•\x00 garbage")  # control chars


class _FakeEngine(TesseractOcr):
    def __init__(self, text: str, *, available: bool = True) -> None:
        self._text = text
        self._available = available
        self.pages: list[int] = []

    def is_available(self) -> bool:
        return self._available

    def ocr_page(self, pdf_path: str, page_index: int) -> str:
        self.pages.append(page_index)
        return self._text


def test_missing_text_layer_is_ocr_repaired() -> None:
    fallback = OcrFallback(_FakeEngine("FORM No. 1\nOCR RECOVERED TITLE\n"))
    pages = [PageText(index=4, printed_page=5, lines=[])]
    repaired, ocr_pages = fallback.fill_pages("source.pdf", pages)
    assert repaired[0].lines == ["FORM No. 1", "OCR RECOVERED TITLE"]
    assert ocr_pages == [4]


def test_usable_pages_are_not_ocr_repaired() -> None:
    engine = _FakeEngine("SHOULD NOT BE USED")
    fallback = OcrFallback(engine)
    pages = [PageText(index=0, printed_page=1, lines=["FORM No. 2", "SUMMONS", "body text"])]
    repaired, ocr_pages = fallback.fill_pages("source.pdf", pages)
    assert repaired == pages
    assert ocr_pages == []
    assert engine.pages == []


def test_ocr_failure_keeps_page_honest(caplog) -> None:
    class _BrokenEngine(_FakeEngine):
        def ocr_page(self, pdf_path: str, page_index: int) -> str:
            raise RuntimeError("tesseract exploded")

    with caplog.at_level(logging.WARNING):
        fallback = OcrFallback(_BrokenEngine(""))
        pages = [PageText(index=0, printed_page=1, lines=[])]
        repaired, ocr_pages = fallback.fill_pages("source.pdf", pages)
    assert repaired == pages
    assert ocr_pages == []


def test_unavailable_toolchain_degrades_to_no_ocr() -> None:
    fallback = OcrFallback(_FakeEngine("", available=False))
    pages = [PageText(index=0, printed_page=1, lines=[])]
    repaired, ocr_pages = fallback.fill_pages("source.pdf", pages)
    assert repaired == pages
    assert ocr_pages == []


def test_normalize_ocr_text_splits_lines() -> None:
    assert normalize_ocr_text("FORM No. 3\n\n  WARRANT   OF ARREST \n") == [
        "FORM No. 3",
        "WARRANT OF ARREST",
    ]


class _CountingTesseract(TesseractOcr):
    """Real tesseract engine that records which pages it was asked to OCR."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def ocr_page(self, pdf_path: str, page_index: int) -> str:
        self.calls.append(page_index)
        return super().ocr_page(pdf_path, page_index)


@pytest.mark.skipif(not TesseractOcr().is_available(), reason="tesseract/pdftoppm not installed")
def test_real_tesseract_repairs_image_only_page(tmp_path: Path) -> None:
    """Controlled live proof of the OCR fallback (B-026..B-029).

    Fixture: page 1 clean text layer, page 2 image-only (text drawn into an
    embedded raster, no text layer), page 3 clean text layer.
    """
    pytest.importorskip("pymupdf")
    from app.forms.pipeline import FormsExtractor

    source = tmp_path / "scanned.pdf"
    source.write_bytes(
        make_scanned_forms_pdf(
            leading_pages=[["FORM No. 1", "NOTICE FOR APPEARANCE", "body text"]],
            scanned_lines=["FORM No. 2", "SUMMONS TO AN ACCUSED PERSON", "See section 63"],
            trailing_pages=[["FORM No. 3", "WARRANT OF ARREST", "body text"]],
        )
    )

    engine = _CountingTesseract()
    extractor = FormsExtractor(page_start=1, page_end=3, ocr=OcrFallback(engine))
    manifest = extractor.extract(str(source), tmp_path / "forms")

    # (a) The image-only page triggered OCR and the repaired text was used:
    # FORM No. 2 and its title exist only inside the raster image.
    assert [form.form_number for form in manifest.forms] == [1, 2, 3]
    assert "SUMMONS" in manifest.forms[1].title.upper()

    # (b) The clean text pages did NOT trigger OCR: the engine ran exactly
    # once, for the image-only page (zero-based index 1).
    assert engine.calls == [1]

    # (c) Honesty: the OCR-repaired form is flagged needs_review with capped
    # confidence; native-text forms keep full confidence and no review flag.
    assert manifest.forms[1].needs_review is True
    assert manifest.forms[1].extraction_confidence <= 0.6
    assert manifest.forms[0].needs_review is False
    assert manifest.forms[0].extraction_confidence == 1.0
    assert manifest.forms[2].needs_review is False
    assert manifest.forms[2].extraction_confidence == 1.0
