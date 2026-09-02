"""Forms extraction domain models (REQUIREMENTS B-001..B-032; ARCHITECTURE §24-§28).

One ``FormRecord`` per detected statutory form; ``FormsManifest`` is the
audit artifact written next to the generated PDFs (``forms_manifest.json``).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

MANIFEST_FILENAME = "forms_manifest.json"


class FormRecord(BaseModel):
    """One extracted form and its full audit metadata (B-017..B-024)."""

    form_number: int
    title: str
    source_page_start: int
    source_page_end: int
    output_filename: str
    byte_size: int
    sha256: str
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    needs_review: bool = False


class ManifestSource(BaseModel):
    """Traceability block: which exact source produced this library."""

    filename: str
    sha256: str
    page_start: int
    page_end: int
    # Act the source actually contains, detected from its text (never from
    # the filename — DECISIONS #74: the supplied file is BNSS, not BNS).
    # None = no act title detected; recorded honestly rather than guessed.
    act_title: str | None = None


class FormsManifest(BaseModel):
    """The generated forms manifest (B-016)."""

    source: ManifestSource
    forms: list[FormRecord] = Field(default_factory=list)

    def by_number(self, form_number: int) -> FormRecord | None:
        for form in self.forms:
            if form.form_number == form_number:
                return form
        return None


class FormListItem(BaseModel):
    """Forms list/search API entry (B-033..B-037)."""

    form_number: int
    title: str
    source_page_start: int
    source_page_end: int
    output_filename: str
    byte_size: int
    needs_review: bool = False


class FormMetadata(FormListItem):
    """Full metadata for one form, including audit fields."""

    sha256: str
    extraction_confidence: float
