"""OCR fallback tests (REQUIREMENTS B-026..B-029; ARCHITECTURE §29)."""

from __future__ import annotations

import logging

from app.forms.ocr import OcrFallback, TesseractOcr, normalize_ocr_text, text_is_usable
from app.ingestion.models import PageText


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
