"""OCR fallback for pages without a usable text layer (B-026..B-029; §29).

OCR is the fallback, never the default: only pages whose extracted text is
empty or garbage are rasterised (pdftoppm) and passed to tesseract. Every
OCR page is logged. The whole path degrades gracefully to "unusable" when
the external tools are absent, so extraction reports honestly instead of
raising.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.ingestion.models import PageText

logger = logging.getLogger(__name__)

# A page whose text is shorter than this, or whose printable ratio is below
# this, is considered garbage.
_MIN_TEXT_CHARS = 8
_MIN_PRINTABLE_RATIO = 0.6


def text_is_usable(text: str) -> bool:
    """Heuristic: does a page's text layer carry real content?"""
    stripped = text.strip()
    if len(stripped) < _MIN_TEXT_CHARS:
        return False
    printable = sum((ch.isprintable() or ch.isspace()) and ord(ch) < 128 for ch in stripped)
    if printable / len(stripped) < _MIN_PRINTABLE_RATIO:
        return False
    # Text that is almost entirely dots (form blanks) is not a usable layer.
    non_dot = stripped.replace(".", "").replace("…", "")
    return len(non_dot.strip()) >= _MIN_TEXT_CHARS


class OcrUnavailableError(RuntimeError):
    """The OCR toolchain (tesseract / pdftoppm) is not installed."""


class TesseractOcr:
    """Rasterise one PDF page and OCR it (pdftoppm + tesseract CLI)."""

    def is_available(self) -> bool:
        return shutil.which("tesseract") is not None and shutil.which("pdftoppm") is not None

    def ocr_page(self, pdf_path: str, page_index: int) -> str:
        """Return OCR text for the zero-based page (raises when unavailable)."""
        if not self.is_available():
            raise OcrUnavailableError("tesseract or pdftoppm is not installed")
        with tempfile.TemporaryDirectory(prefix="nyay-ocr-") as tmp:
            prefix = str(Path(tmp) / "page")
            subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    str(page_index + 1),
                    "-l",
                    str(page_index + 1),
                    "-r",
                    "300",
                    "-png",
                    "-singlefile",
                    pdf_path,
                    prefix,
                ],
                check=True,
                capture_output=True,
            )
            result = subprocess.run(
                ["tesseract", f"{prefix}.png", "stdout"],
                check=True,
                capture_output=True,
                text=True,
            )
        return result.stdout


def normalize_ocr_text(text: str) -> list[str]:
    """Split raw OCR output into page lines (same shape as pypdf extraction)."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return [line for line in lines if line]


class OcrFallback:
    """Applies OCR to pages whose text layer is missing or garbage.

    Logs each page that requires OCR (B-029) and never raises: a page that
    cannot be OCR'd keeps its original (empty/garbage) text and is reported
    through the returned per-page list.
    """

    def __init__(self, engine: TesseractOcr | None = None) -> None:
        self._engine = engine or TesseractOcr()

    def fill_pages(self, pdf_path: str, pages: list[PageText]) -> tuple[list[PageText], list[int]]:
        """Return (pages with OCR applied where needed, OCR'd page indexes)."""
        if not self._engine.is_available():
            logger.warning("OCR fallback unavailable: tesseract/pdftoppm not installed")
            return pages, []
        repaired: list[PageText] = []
        ocr_pages: list[int] = []
        for page in pages:
            joined = "\n".join(page.lines)
            if text_is_usable(joined):
                repaired.append(page)
                continue
            logger.info(
                "OCR fallback engaged",
                extra={"page": page.index + 1, "printed_page": page.printed_page},
            )
            try:
                text = self._engine.ocr_page(pdf_path, page.index)
            except Exception:
                logger.warning("OCR failed for page", extra={"page": page.index + 1})
                repaired.append(page)
                continue
            lines = normalize_ocr_text(text)
            repaired.append(PageText(index=page.index, printed_page=page.printed_page, lines=lines))
            ocr_pages.append(page.index)
        return repaired, ocr_pages
