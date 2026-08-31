"""Document chunking (REQUIREMENTS A5-003; plan 5.5).

User documents are NOT statutory text: they have no reliable section
structure, so the Phase 2 structure-aware statutory chunker must not be
applied (plan 5.5, "Do not accidentally apply a document strategy to BNS"
— and vice versa). This chunker is page-aware and size-bounded only.
"""

from __future__ import annotations

from app.documents.models import DocumentChunk
from app.ingestion.models import PageText

MAX_CHUNK_CHARS = 1200
_OVERLAP_CHARS = 0


def chunk_document_pages(
    pages: list[PageText], *, document_id: str, session_id: str, source_uri: str
) -> list[DocumentChunk]:
    """Split extracted pages into size-bounded chunks.

    Chunk ids are deterministic (document + page + sequence) so re-ingestion
    is idempotent. The page encoded in the id is the 1-based page number —
    the same number carried by ``page_start``/``page_end`` and printed in
    document citations — never the 0-based ``PageText.index`` (D-076).
    """
    chunks: list[DocumentChunk] = []
    for page in pages:
        text = "\n".join(page.lines).strip()
        if not text:
            continue
        parts = _split(text, MAX_CHUNK_CHARS)
        for seq, part in enumerate(parts):
            chunks.append(
                DocumentChunk(
                    chunk_id=f"{document_id}-p{page.index + 1:04d}-{seq:03d}",
                    document_id=document_id,
                    session_id=session_id,
                    page_start=page.index + 1,
                    page_end=page.index + 1,
                    text=part,
                    source_uri=f"{source_uri}#page={page.index + 1}",
                )
            )
    return chunks


def _split(text: str, max_chars: int) -> list[str]:
    """Split one page's text into parts no longer than ``max_chars``.

    Splits on paragraph boundaries where possible, then sentences, then
    hard-wraps pathological unbroken text.
    """
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            parts.append(current)
        current = ""
        if len(paragraph) <= max_chars:
            current = paragraph
            continue
        # Paragraph alone exceeds the bound: sentence-split it.
        parts.extend(_split_sentences(paragraph, max_chars))
    if current:
        parts.append(current)
    return [p for p in (part.strip() for part in parts) if p]


def _split_sentences(text: str, max_chars: int) -> list[str]:
    import re

    # Include the Indic danda so Indic-script documents split into
    # sentences instead of one giant block (multilingual audit).
    sentences = re.split(r"(?<=[.!?।॥])\s+", text)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            parts.append(current)
        while len(sentence) > max_chars:
            parts.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        current = sentence
    if current:
        parts.append(current)
    return parts
