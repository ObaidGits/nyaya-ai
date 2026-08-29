"""Structure-aware statutory chunker (REQUIREMENTS A1-001, A1-020..A1-029).

Rules (locked in DECISIONS.md D-019/D-020):

* a short section is one chunk (A1-020/A1-021);
* a long section splits only at subsection boundaries, and adjacent small
  subsection groups are merged back together while they fit (A1-022);
* text is never split mid-sentence (A1-023) — a legal unit with no internal
  boundary stays whole even if oversized;
* provisos, exceptions, explanations and illustrations stay attached to the
  subsection chunk they follow (A1-024..A1-028);
* no arbitrary token/character overlap: parent structural context is carried
  as metadata on every chunk (D-020).

``chunk_id`` is deterministic: ``<act_short>-s<sec>-<part>`` where ``part``
is the 1-based part index within the section.
"""

from __future__ import annotations

from app.ingestion.models import Block, BlockKind, Chunk, ParsedAct, Section
from app.ingestion.references import detect_references

DEFAULT_MAX_CHARS = 4000


class StructureAwareChunker:
    """Split a parsed act into metadata-complete chunks."""

    def __init__(self, max_chars: int = DEFAULT_MAX_CHARS) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        self.max_chars = max_chars
        self.warnings: list[str] = []

    def chunk(self, act: ParsedAct, source_uri_base: str, ingested_at: str) -> list[Chunk]:
        chunks: list[Chunk] = []
        for section in act.sections:
            chunks.extend(self._chunk_section(act, section, source_uri_base, ingested_at))
        return chunks

    # -- section-level -------------------------------------------------------

    def _chunk_section(
        self,
        act: ParsedAct,
        section: Section,
        source_uri_base: str,
        ingested_at: str,
    ) -> list[Chunk]:
        text = section.text
        if len(text) <= self.max_chars:
            return [
                self._build_chunk(act, section, section.blocks, 1, 1, source_uri_base, ingested_at)
            ]

        groups = self._split_at_subsection_boundaries(section)
        if len(groups) <= 1:
            # No legal boundary to split at: keep the section whole.
            self.warnings.append(
                f"section {section.number} exceeds {self.max_chars} chars with no "
                f"subsection boundary; kept whole"
            )
            return [
                self._build_chunk(act, section, section.blocks, 1, 1, source_uri_base, ingested_at)
            ]

        merged = self._merge_adjacent(groups)
        return [
            self._build_chunk(act, section, group, i + 1, len(merged), source_uri_base, ingested_at)
            for i, group in enumerate(merged)
        ]

    def _split_at_subsection_boundaries(self, section: Section) -> list[list[Block]]:
        """Group blocks by subsection; components stay with their subsection."""
        groups: list[list[Block]] = []
        current: list[Block] = []
        for block in section.blocks:
            prev_body = next((b for b in reversed(current) if b.kind == BlockKind.BODY), None)
            new_subsection = (
                block.kind == BlockKind.BODY
                and block.subsection is not None
                and (prev_body is None or prev_body.subsection != block.subsection)
            )
            if current and new_subsection:
                groups.append(current)
                current = []
            current.append(block)
        if current:
            groups.append(current)
        return groups

    def _merge_adjacent(self, groups: list[list[Block]]) -> list[list[Block]]:
        """Merge neighbouring groups while the result fits and shares a
        subsection label, so tiny subsections do not become tiny chunks."""
        merged: list[list[Block]] = []
        for group in groups:
            if (
                merged
                and self._fits(merged[-1], group)
                and self._same_subsection(merged[-1], group)
            ):
                merged[-1].extend(group)
            else:
                merged.append(list(group))
        return merged

    def _fits(self, a: list[Block], b: list[Block]) -> bool:
        return len(self._text(a)) + len(self._text(b)) <= self.max_chars

    @staticmethod
    def _same_subsection(a: list[Block], b: list[Block]) -> bool:
        sub_a = {blk.subsection for blk in a if blk.kind == BlockKind.BODY}
        sub_b = {blk.subsection for blk in b if blk.kind == BlockKind.BODY}
        return sub_a == sub_b

    # -- chunk construction ---------------------------------------------------

    def _build_chunk(
        self,
        act: ParsedAct,
        section: Section,
        blocks: list[Block],
        part: int,
        total_parts: int,
        source_uri_base: str,
        ingested_at: str,
    ) -> Chunk:
        text = self._text(blocks)
        body_blocks = [b for b in blocks if b.kind == BlockKind.BODY]
        subsection = body_blocks[0].subsection if body_blocks else None
        clause = body_blocks[0].clause if body_blocks else None
        if total_parts > 1:
            subs = [b.subsection for b in body_blocks if b.subsection]
            if subs:
                subsection = subs[0] if len(set(subs)) == 1 else f"{subs[0]}-{subs[-1]}"
        pages = [b.page for b in blocks if b.page] or [section.page_start]
        suffix = "" if total_parts == 1 else f" (part {part} of {total_parts})"
        chunk_id = f"{act.act_short.lower()}-s{section.number}-{part:03d}"
        return Chunk(
            chunk_id=chunk_id,
            act=act.act,
            act_short=act.act_short,
            chapter=section.chapter_number,
            chapter_title=section.chapter_title or None,
            section_number=str(section.number),
            section_title=(section.title + suffix) if section.title else None,
            subsection=subsection,
            clause=clause,
            text=text,
            has_illustration=any(b.kind == BlockKind.ILLUSTRATION for b in blocks),
            has_proviso=any(b.kind == BlockKind.PROVISO for b in blocks),
            has_exception=any(b.kind == BlockKind.EXCEPTION for b in blocks),
            page_start=min(pages),
            page_end=max(pages),
            source_uri=f"{source_uri_base}#page={min(pages)}-{max(pages)}",
            ingested_at=ingested_at,
            references=detect_references(text),
            needs_review=not section.title_confident,
        )

    @staticmethod
    def _text(blocks: list[Block]) -> str:
        return "\n".join(b.text for b in blocks if b.text.strip())
