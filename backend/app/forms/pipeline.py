"""Forms extraction pipeline (REQUIREMENTS B-001..B-032; ARCHITECTURE §24-§30).

Deterministic pipeline: source PDF → page slice → (OCR fallback) → boundary
detection → page-perfect form PDFs → manifest. Running it twice on identical
input produces byte-identical outputs and the same manifest (B-030/B-031) —
outputs are rewritten in a stable order with a fresh ``PdfWriter``, which
does not embed timestamps.
"""

from __future__ import annotations

import hashlib
import logging
import re
from itertools import pairwise
from pathlib import Path

from app.core.errors import AppError
from app.forms.detector import DetectedForm, detect_forms
from app.forms.models import (
    MANIFEST_FILENAME,
    FormRecord,
    FormsManifest,
    ManifestSource,
)
from app.forms.naming import ensure_unique, form_filename
from app.forms.ocr import OcrFallback
from app.ingestion.cleaning import clean_page_lines
from app.ingestion.models import PageText

logger = logging.getLogger(__name__)

# "FORM No.1" (number glued to the label, no space — Gazette p.190): the
# generic inline-chrome rule that strips glued page numbers (".46" at line
# end) would eat ".1" and the detector would lose FORM No. 1. Normalize the
# header to the spaced form BEFORE cleaning so the number survives.
_FORM_HEADER_GLUE_RE = re.compile(r"^(\s*FORM\s+No\.?)\s*(\d+)\s*$", re.IGNORECASE)


def _normalize_form_header(raw_lines: list[str]) -> list[str]:
    return [_FORM_HEADER_GLUE_RE.sub(r"\1 \2", line) for line in raw_lines]


# Assignment-defined processing range (DECISIONS D-002). Content drives the
# extraction; this range only bounds it.
DEFAULT_FORMS_PAGE_START = 190
DEFAULT_FORMS_PAGE_END = 249


class FormsExtractionError(AppError):
    """The forms pipeline cannot produce trustworthy output."""

    status_code = 500
    code = "FORMS_EXTRACTION_FAILED"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _extract_pdf_pages(source_path: str, start_index: int, end_index: int) -> bytes:
    """Render pages [start_index, end_index] into one fresh PDF (page-perfect,
    no re-rendering: original page objects are copied as-is, B-003/B-004)."""
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(source_path)
    writer = PdfWriter()
    for index in range(start_index, end_index + 1):
        writer.add_page(reader.pages[index])
    from io import BytesIO

    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class FormsExtractor:
    """Extract the statutory forms library from the exact source PDF."""

    def __init__(
        self,
        *,
        page_start: int = DEFAULT_FORMS_PAGE_START,
        page_end: int = DEFAULT_FORMS_PAGE_END,
        ocr: OcrFallback | None = None,
    ) -> None:
        self._page_start = page_start
        self._page_end = page_end
        self._ocr = ocr or OcrFallback()

    def _read_pages(self, source: Path) -> list[PageText]:
        """Read every source page (per-page, so one blank page never aborts).

        Unlike the statute extractor there is no global "no usable text"
        gate: a textless page stays empty here and the OCR fallback repairs
        it, keeping forms extraction honest page-by-page.
        """
        from pypdf import PdfReader

        reader = PdfReader(str(source))
        pages: list[PageText] = []
        for index, page in enumerate(reader.pages):
            raw = (page.extract_text() or "").splitlines()
            raw = _normalize_form_header(raw)
            printed = index + 1
            if raw and raw[0].strip().isdigit():
                printed = int(raw[0].strip())
            pages.append(PageText(index=index, printed_page=printed, lines=clean_page_lines(raw)))
        return pages

    def extract(self, source_path: str, output_dir: Path) -> FormsManifest:
        """Run the full pipeline; write form PDFs + manifest into output_dir."""
        source = Path(source_path)
        if not source.is_file():
            raise FormsExtractionError(
                f"forms source not found: {source_path}",
                code="FORMS_SOURCE_MISSING",
            )

        try:
            all_pages = self._read_pages(source)
        except FormsExtractionError:
            raise
        except Exception as exc:
            raise FormsExtractionError(
                f"cannot extract text from forms source: {source_path}",
                code="FORMS_SOURCE_UNREADABLE",
            ) from exc

        page_count = len(all_pages)
        if page_count < self._page_start:
            raise FormsExtractionError(
                f"source has {page_count} pages; forms range needs page {self._page_start}",
                code="FORMS_RANGE_INVALID",
            )
        last_index = min(self._page_end, page_count) - 1
        first_index = self._page_start - 1
        pages = all_pages[first_index : last_index + 1]

        pages, ocr_pages = self._ocr.fill_pages(str(source), pages)
        if ocr_pages:
            logger.info(
                "OCR fallback used for forms pages",
                extra={"pages": [i + 1 for i in ocr_pages]},
            )

        # Blank/continuation-only pages never break detection: a page with no
        # usable text is preserved as a continuation (honest, needs_review).
        forms = detect_forms(pages)
        if not forms:
            raise FormsExtractionError(
                "no forms detected in the configured range",
                code="FORMS_NONE_DETECTED",
            )

        # Validation (assignment "Validation" scope): every form must map
        # back to real source pages and every page must belong to a form.
        self._validate(forms, pages, first_index)

        output_dir.mkdir(parents=True, exist_ok=True)
        records: list[FormRecord] = []
        taken: set[str] = set()
        for form in forms:
            filename = ensure_unique(form_filename(form.form_number, form.title), taken)
            taken.add(filename)
            # Page indexes are already absolute (0-based) source page numbers.
            data = _extract_pdf_pages(
                str(source),
                form.start_page_index,
                form.end_page_index,
            )
            (output_dir / filename).write_bytes(data)
            records.append(
                FormRecord(
                    form_number=form.form_number,
                    title=form.title,
                    source_page_start=all_pages[form.start_page_index].printed_page,
                    source_page_end=all_pages[form.end_page_index].printed_page,
                    output_filename=filename,
                    byte_size=len(data),
                    sha256=_sha256_bytes(data),
                    extraction_confidence=form.confidence,
                    needs_review=form.needs_review,
                )
            )

        manifest = FormsManifest(
            source=ManifestSource(
                filename=source.name,
                sha256=_sha256_bytes(source.read_bytes()),
                page_start=self._page_start,
                page_end=last_index + 1,
            ),
            forms=records,
        )
        self._write_manifest(output_dir, manifest)
        return manifest

    def _validate(self, forms: list[DetectedForm], pages: list[PageText], first_index: int) -> None:
        """Fail clearly when boundaries cannot be trusted."""
        numbers = [form.form_number for form in forms]
        if len(numbers) != len(set(numbers)):
            duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
            raise FormsExtractionError(
                f"duplicate form numbers detected: {duplicates}",
                code="FORMS_BOUNDARIES_UNTRUSTED",
            )
        # Non-decreasing start pages; each form maps back to source pages.
        for previous, current in pairwise(forms):
            if current.start_page_index < previous.end_page_index:
                raise FormsExtractionError(
                    f"overlapping form boundaries around page {current.start_page_index + 1}",
                    code="FORMS_BOUNDARIES_UNTRUSTED",
                )
        if pages and forms:
            last_page_index = pages[-1].index
            if forms[-1].end_page_index > last_page_index:
                raise FormsExtractionError(
                    "form boundaries exceed the configured page range",
                    code="FORMS_BOUNDARIES_UNTRUSTED",
                )

    def _write_manifest(self, output_dir: Path, manifest: FormsManifest) -> None:
        """Write ``forms_manifest.json`` deterministically (B-016/B-032)."""
        payload = manifest.model_dump_json(indent=2, exclude_none=False)
        path = output_dir / MANIFEST_FILENAME
        path.write_text(payload + "\n", encoding="utf-8")
