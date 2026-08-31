"""Chunk corpus store for retrieval (ARCHITECTURE §9, Phase 2 artifact).

Loads the Phase 2 chunk JSONL (``data/processed/<spec>_chunks.jsonl``,
produced deterministically by ``scripts/ingest.py``) and provides:

* chunk-id lookup,
* deterministic section lookup by (act_short, section number),
* server-side metadata filter matching (D-018).

The store is the retrieval-side view of the indexed corpus; ingestion
architecture is untouched.
"""

from __future__ import annotations

from pathlib import Path

from app.ingestion.models import Chunk
from app.retrieval.models import MetadataFilter


class ChunkStore:
    """In-memory view of the indexed chunk corpus."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self._chunks = chunks
        self._by_id = {c.chunk_id: c for c in chunks}

    @classmethod
    def from_jsonl(cls, path: Path) -> ChunkStore:
        chunks = [Chunk.model_validate_json(line) for line in path.read_text().splitlines() if line]
        return cls(chunks)

    @property
    def chunks(self) -> list[Chunk]:
        return self._chunks

    def get(self, chunk_id: str) -> Chunk | None:
        return self._by_id.get(chunk_id)

    def matches(self, chunk: Chunk, flt: MetadataFilter | None) -> bool:
        """True if the chunk passes the metadata filter (D-018)."""
        if flt is None:
            return True
        return (
            (flt.act is None or chunk.act == flt.act)
            and (flt.act_short is None or chunk.act_short == flt.act_short)
            and (flt.chapter is None or chunk.chapter == flt.chapter)
            and (flt.section_number is None or chunk.section_number == flt.section_number)
        )

    def act_shorts(self) -> set[str]:
        """Distinct act short codes present in the corpus."""
        return {c.act_short for c in self._chunks}

    def section_lookup(self, section_number: str, *, act_short: str | None = None) -> list[Chunk]:
        """Deterministic section lookup (D-017) — no similarity involved.

        Returns all chunks of the section in deterministic chunk_id order
        (multi-part sections return every part).
        """
        return sorted(
            (
                c
                for c in self._chunks
                if c.section_number == section_number
                and (act_short is None or c.act_short == act_short)
            ),
            key=lambda c: c.chunk_id,
        )

    def filter(self, flt: MetadataFilter | None) -> list[Chunk]:
        if flt is None:
            return list(self._chunks)
        return [c for c in self._chunks if self.matches(c, flt)]
