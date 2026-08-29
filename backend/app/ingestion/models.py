"""Domain models for the structure-aware ingestion pipeline (REQUIREMENTS A1-*).

The parser, chunker and pipeline exchange these typed models. They carry the
full chunk metadata schema required by `docs/REQUIREMENTS.md` (A1-002..A1-019,
A1-037/A1-038) plus the source-identity information needed for citations and
reproducible re-ingestion (SRC-009).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field


class BlockKind(StrEnum):
    """Kind of a legal text block inside a section."""

    BODY = "body"
    PROVISO = "proviso"
    EXCEPTION = "exception"
    EXPLANATION = "explanation"
    ILLUSTRATION = "illustration"


class CorpusSpec(BaseModel):
    """Configuration describing the expected authoritative corpus.

    The pipeline is source-driven: nothing about a specific PDF filename or
    layout is hardcoded. A replacement source PDF is validated against this
    spec (title pattern + structural invariants), never against its filename.

    `title_pattern` is matched (case-insensitive) against the act title found
    on the opening pages of the extracted text.
    """

    act: str
    act_short: str
    title_pattern: str
    min_sections: int = 100
    min_pages: int = 50

    @classmethod
    def bns(cls) -> Self:
        """Spec for the required assignment corpus (Bharatiya Nyaya Sanhita)."""
        return cls(
            act="Bharatiya Nyaya Sanhita, 2023",
            act_short="BNS",
            title_pattern=r"bhara\s*tiy\s*a\s*nyaya\s*sanhita",
            min_sections=300,
            min_pages=100,
        )

    @classmethod
    def bnss_dev_fixture(cls) -> Self:
        """Spec for the temporary development-fixture PDF only.

        The file currently in ``data/raw/`` is BNSS (Bharatiya Nagarik
        Suraksha Sanhita), NOT the required BNS source. It is used purely as a
        layout-compatible development fixture while the correct BNS PDF is
        awaited from DhronAI. Final BNS corpus validation stays BLOCKED.
        """
        return cls(
            act="Bharatiya Nagarik Suraksha Sanhita, 2023",
            act_short="BNSS",
            title_pattern=r"bhara\s*tiy\s*a\s*nagarik\s*suraksha\s*sanhita",
            min_sections=400,
            min_pages=150,
        )


class PageText(BaseModel):
    """One extracted, cleaned source page.

    `index` is the zero-based PDF page index; `printed_page` is the page
    number printed on the page (falls back to ``index + 1`` when absent).
    """

    index: int
    printed_page: int
    lines: list[str] = Field(default_factory=list)


class SourceIdentity(BaseModel):
    """Identity of the ingested source document (SRC-009, citations)."""

    filename: str
    sha256: str
    page_count: int
    act_title_detected: str | None = None


class ValidationResult(BaseModel):
    """Outcome of source/structure validation.

    `fatal` problems abort ingestion; non-fatal problems are recorded per
    section (e.g. uncertain marginal-note association) and surfaced honestly.
    """

    ok: bool
    act_title_detected: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Block(BaseModel):
    """A structural text block inside a section."""

    kind: BlockKind
    text: str
    page: int  # printed source page
    subsection: str | None = None  # e.g. "(1)"
    clause: str | None = None  # e.g. "(a)"


class Section(BaseModel):
    """A parsed statutory section with its structural components."""

    number: int
    title: str | None = None
    title_confident: bool = False
    chapter_number: str | None = None  # roman numeral, e.g. "XXVI"
    chapter_title: str | None = None
    page_start: int
    page_end: int
    blocks: list[Block] = Field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks if b.text.strip())

    @property
    def has_proviso(self) -> bool:
        return any(b.kind == BlockKind.PROVISO for b in self.blocks)

    @property
    def has_exception(self) -> bool:
        return any(b.kind == BlockKind.EXCEPTION for b in self.blocks)

    @property
    def has_explanation(self) -> bool:
        return any(b.kind == BlockKind.EXPLANATION for b in self.blocks)

    @property
    def has_illustration(self) -> bool:
        return any(b.kind == BlockKind.ILLUSTRATION for b in self.blocks)


class ParsedAct(BaseModel):
    """Full parsed statute structure."""

    act: str
    act_short: str
    act_title_detected: str
    sections: list[Section] = Field(default_factory=list)
    statute_end_page: int | None = None


class Chunk(BaseModel):
    """A retrieval chunk with the complete required metadata schema (A1-002)."""

    chunk_id: str
    act: str
    act_short: str
    chapter: str | None
    chapter_title: str | None
    section_number: str
    section_title: str | None
    subsection: str | None
    clause: str | None
    text: str
    has_illustration: bool
    has_proviso: bool
    has_exception: bool
    page_start: int
    page_end: int
    source_uri: str
    ingested_at: str
    references: list[str] = Field(default_factory=list)
    needs_review: bool = False


class IngestionResult(BaseModel):
    """Summary of one ingestion run."""

    source: SourceIdentity
    validation: ValidationResult
    section_count: int
    chunk_count: int
    output_path: str | None = None
