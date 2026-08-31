"""BM25 sparse retrieval (DECISIONS.md D-013, ARCHITECTURE §11).

Self-contained Okapi BM25 (k1=1.5, b=0.75 — standard parameters) over the
indexed chunk corpus. No external dependency: the corpus is small enough
that an in-process BM25 index is the correct sparse retrieval layer, and
it must support exact legal identifiers ("section 103", "103(1)") which
the legal-aware tokenizer preserves.

The retrieval contract is the ``SparseRetriever`` protocol; a Qdrant
full-text/sparse-vector backend can replace ``Bm25SparseIndex`` behind the
same interface (D-013: exact library is implementation detail).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Protocol

from app.ingestion.models import Chunk
from app.retrieval.models import MetadataFilter

K1 = 1.5
B = 0.75

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\([0-9]+\))?")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens; keeps legal identifiers intact ("103(1)")."""
    return _TOKEN_RE.findall(text.lower())


def _chunk_document(chunk: Chunk) -> list[str]:
    """Searchable text for a chunk: section identifier + title + body.

    The section number and title are repeated so exact-identifier queries
    ("section 103") rank the right section even when the body never
    restates the number.
    """
    prefix = f"section {chunk.section_number} {chunk.section_title or ''}"
    return tokenize(f"{prefix} {prefix} {chunk.text}")


class SparseRetriever(Protocol):
    """Sparse/BM25 retrieval seam."""

    def search(self, query: str, flt: MetadataFilter | None, top_k: int) -> list[str]: ...


class Bm25SparseIndex:
    """BM25 index over the chunk corpus, filter-aware (D-018).

    Filters are applied server-side before scoring: filtered-out chunks
    never participate in ranking.
    """

    def __init__(self, chunks: list[Chunk]) -> None:
        self._docs = {c.chunk_id: _chunk_document(c) for c in chunks}
        self._chunks = {c.chunk_id: c for c in chunks}
        self._doc_lens = {cid: len(doc) for cid, doc in self._docs.items()}
        self._avgdl = sum(self._doc_lens.values()) / len(self._doc_lens) if self._doc_lens else 0.0
        self._df: Counter[str] = Counter()
        for doc in self._docs.values():
            self._df.update(set(doc))
        self._n = len(self._docs)

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        return math.log(1.0 + (self._n - df + 0.5) / (df + 0.5))

    def score(self, chunk_id: str, query_tokens: list[str]) -> float:
        doc = self._docs[chunk_id]
        freq = Counter(doc)
        dl = self._doc_lens[chunk_id]
        total = 0.0
        for term in query_tokens:
            tf = freq.get(term, 0)
            if tf == 0:
                continue
            idf = self._idf(term)
            # If avgdl is 0 (empty corpus), skip the length-normalization term.
            denom = tf + K1 * (1.0 - B + B * dl / self._avgdl) if self._avgdl else tf + K1
            total += idf * tf * (K1 + 1.0) / denom
        return total

    def search(self, query: str, flt: MetadataFilter | None, top_k: int) -> list[str]:
        """Return top-k chunk ids ranked by BM25 relevance."""
        query_tokens = tokenize(query)
        if not query_tokens or top_k <= 0:
            return []
        candidates = [
            cid
            for cid, chunk in self._chunks.items()
            if flt is None
            or (
                (flt.act is None or chunk.act == flt.act)
                and (flt.act_short is None or chunk.act_short == flt.act_short)
                and (flt.chapter is None or chunk.chapter == flt.chapter)
                and (flt.section_number is None or chunk.section_number == flt.section_number)
            )
        ]
        scored = sorted(
            ((cid, self.score(cid, query_tokens)) for cid in candidates),
            key=lambda pair: (-pair[1], pair[0]),
        )
        return [cid for cid, _score in scored[:top_k] if _score > 0]
