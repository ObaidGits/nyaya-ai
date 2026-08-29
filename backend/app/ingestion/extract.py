"""PDF text extraction layer (thin; separated for testability).

The parser and chunker operate on ``PageText`` models and never touch a PDF
library. ``PypdfPageExtractor`` is the production extractor; tests inject a
fake extractor so the pipeline is testable without the real source PDF.

Heavy imports (pypdf) are performed lazily so importing the package stays
cheap and optional dependencies remain optional.
"""

from __future__ import annotations

from typing import Protocol

from app.core.errors import AppError
from app.ingestion.cleaning import clean_page_lines
from app.ingestion.models import PageText


class ExtractionError(AppError):
    """Raised when the source PDF cannot be read or has no usable text."""


class PageExtractor(Protocol):
    """Extracts cleaned pages from a source document."""

    def extract(self, source_path: str) -> list[PageText]: ...


class PypdfPageExtractor:
    """Production extractor over pypdf.

    Reads the raw text layer per page, records the printed page number
    (leading bare-number line) and applies Gazette cleanup rules.
    """

    def extract(self, source_path: str) -> list[PageText]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise ExtractionError(
                "pypdf is not installed",
                code="INGESTION_EXTRACTION_FAILED",
            ) from exc

        try:
            reader = PdfReader(source_path)
            pages: list[PageText] = []
            for index, page in enumerate(reader.pages):
                raw = (page.extract_text() or "").splitlines()
                printed = index + 1
                if raw and raw[0].strip().isdigit():
                    printed = int(raw[0].strip())
                pages.append(
                    PageText(
                        index=index,
                        printed_page=printed,
                        lines=clean_page_lines(raw),
                    )
                )
        except ExtractionError:
            raise
        except Exception as exc:
            raise ExtractionError(
                f"failed to extract text from {source_path}",
                code="INGESTION_EXTRACTION_FAILED",
            ) from exc

        if not any(page.lines for page in pages):
            raise ExtractionError(
                f"source has no usable text layer: {source_path}",
                code="INGESTION_EXTRACTION_FAILED",
            )
        return pages
