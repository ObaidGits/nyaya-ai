"""Generated forms library: loading, querying, serving (B-033..B-040).

The library is a directory of ``FORM-*.pdf`` files plus
``forms_manifest.json`` produced by the extraction pipeline (or bootstrap
script). The API layer serves from this library; extraction itself runs
out-of-band (``scripts/extract_forms.py``).
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path

from app.core.errors import AppError
from app.forms.models import (
    MANIFEST_FILENAME,
    FormListItem,
    FormMetadata,
    FormRecord,
    FormsManifest,
)
from app.forms.naming import slugify_title

logger = logging.getLogger(__name__)

_SEARCH_STOPWORDS = {"form", "no", "the", "of", "and", "for", "a", "an", "to", "in"}


class FormsNotConfiguredError(AppError):
    """No forms library is configured on this instance."""

    status_code = 503
    code = "FORMS_NOT_CONFIGURED"


class FormNotFoundError(AppError):
    """The requested form id does not exist."""

    status_code = 404
    code = "FORM_NOT_FOUND"


class FormsLibrary:
    """Read-only view over a generated forms directory."""

    def __init__(self, root: Path) -> None:
        self._root = root
        manifest_path = root / MANIFEST_FILENAME
        if not manifest_path.is_file():
            raise FormsNotConfiguredError(
                "Forms library manifest is missing.",
                code="FORMS_NOT_CONFIGURED",
            )
        self._manifest = FormsManifest.model_validate_json(manifest_path.read_text())
        self._forms_dir = root
        self._by_number = {form.form_number: form for form in self._manifest.forms}
        if len(self._by_number) != len(self._manifest.forms):
            raise AppError(
                "Forms manifest contains duplicate form numbers.",
                status_code=500,
                code="FORMS_MANIFEST_INVALID",
            )

    @property
    def manifest(self) -> FormsManifest:
        return self._manifest

    def list_forms(
        self, *, query: str | None = None, needs_review: bool | None = None
    ) -> list[FormListItem]:
        """List forms, optionally filtered by title/number query and review flag."""
        items = [
            FormListItem(
                form_number=form.form_number,
                title=form.title,
                source_page_start=form.source_page_start,
                source_page_end=form.source_page_end,
                output_filename=form.output_filename,
                byte_size=form.byte_size,
                needs_review=form.needs_review,
            )
            for form in self._manifest.forms
        ]
        if needs_review is not None:
            items = [item for item in items if item.needs_review == needs_review]
        if query:
            items = [item for item in items if self._matches(item, query)]
        return items

    def get_metadata(self, form_number: int) -> FormMetadata:
        form = self._require(form_number)
        return FormMetadata(
            form_number=form.form_number,
            title=form.title,
            source_page_start=form.source_page_start,
            source_page_end=form.source_page_end,
            output_filename=form.output_filename,
            byte_size=form.byte_size,
            needs_review=form.needs_review,
            sha256=form.sha256,
            extraction_confidence=form.extraction_confidence,
        )

    def read_form(self, form_number: int) -> tuple[str, bytes]:
        """Return (filename, pdf_bytes) for one form (B-038)."""
        form = self._require(form_number)
        path = self._form_path(form.output_filename)
        if not path.is_file():
            logger.error(
                "manifest form file missing",
                extra={"form": form.form_number, "file": form.output_filename},
            )
            raise AppError(
                "The form file is missing from the library.",
                status_code=500,
                code="FORM_FILE_MISSING",
            )
        return form.output_filename, path.read_bytes()

    def build_zip(self) -> bytes:
        """Bundle every form (plus the manifest) into one ZIP (B-039)."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for form in self._manifest.forms:
                path = self._form_path(form.output_filename)
                if path.is_file():
                    archive.write(path, arcname=form.output_filename)
                else:
                    logger.warning(
                        "form missing during bulk zip", extra={"form": form.output_filename}
                    )
            archive.writestr(MANIFEST_FILENAME, (self._root / MANIFEST_FILENAME).read_text())
        return buffer.getvalue()

    def _require(self, form_number: int) -> FormRecord:
        form = self._by_number.get(form_number)
        if form is None:
            raise FormNotFoundError(
                f"Form {form_number} was not found.",
                code="FORM_NOT_FOUND",
            )
        return form

    def _form_path(self, filename: str) -> Path:
        # Manifest filenames are generated by the naming module; still refuse
        # anything path-shaped as defense in depth.
        if "/" in filename or "\\" in filename or filename != Path(filename).name:
            raise AppError(
                "Invalid form filename.",
                status_code=500,
                code="FORMS_MANIFEST_INVALID",
            )
        return self._forms_dir / filename

    @staticmethod
    def _matches(item: FormListItem, query: str) -> bool:
        """Case-insensitive match on title, number, and slug tokens."""
        needle = query.strip().lower()
        if not needle:
            return True
        if str(item.form_number) == needle:
            return True
        haystack = f"{item.title} {slugify_title(item.title)}".lower()
        if needle in haystack:
            return True
        tokens = {
            token
            for token in re.split(r"[^a-z0-9]+", haystack)
            if token and token not in _SEARCH_STOPWORDS
        }
        return bool(tokens & set(re.split(r"[^a-z0-9]+", needle)))


def build_forms_library(root: str | Path) -> FormsLibrary | None:
    """Load the library when configured; None keeps the 503 seam honest."""
    if not root:
        return None
    path = Path(root)
    if not path.is_dir():
        return None
    return FormsLibrary(path)
