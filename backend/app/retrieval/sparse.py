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

# Function words removed at tokenize time. Deliberately excludes words with
# legal meaning ("will" = testament, "shall"/"may"/"must" = statutory
# obligation) — those stay indexed; BM25's IDF already discounts them.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "nor",
        "so",
        "yet",
        "if",
        "then",
        "than",
        "that",
        "these",
        "those",
        "of",
        "at",
        "by",
        "for",
        "with",
        "about",
        "against",
        "between",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "to",
        "from",
        "up",
        "down",
        "in",
        "out",
        "on",
        "off",
        "over",
        "under",
        "again",
        "further",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "all",
        "any",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "not",
        "only",
        "own",
        "same",
        "too",
        "very",
        "just",
        "now",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "me",
        "him",
        "us",
        "them",
        "my",
        "your",
        "his",
        "hers",
        "its",
        "ours",
        "theirs",
        "mine",
        "yours",
        "what",
        "which",
        "who",
        "whom",
        "whose",
        "am",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "having",
        "do",
        "does",
        "did",
        "doing",
        "would",
        "could",
        "should",
    ]
)

# Layman word → statutory term (sparse-precision audit, remediation C).
# Applied to the STEMMED token, symmetrically to documents and queries.
# Entries are limited to high-frequency pairs where the corpus wording and
# the layman phrasing share no stem:
#   kill*  → "murder"  — BNS s.103 says "murder", never "kill".
#   thief/steal/stole/stolen → "theft" — BNS s.303 says "theft".
#   cruel  → "cruelty" — BNS s.85 says "cruelty by husband".
_LAYMAN_STATUTORY_MAP = {
    "kill": "murder",
    "thief": "theft",
    "steal": "theft",
    "stol": "theft",  # "stole" (trailing-e stem)
    "stolen": "theft",
    "cruel": "cruelty",
}


def _stem(token: str) -> str:
    """Light suffix stemmer: plurals, -ing/-ed/-ly, trailing 'e'.

    Deliberately conservative (no -er/-ation stripping): legal terms such
    as "murder", "possession" or "defamation" must not be distorted. The
    trailing-'e' strip unifies "file"/"filed"/"filing" and
    "notice"/"notices" onto one stem, which heavier rules would miss.
    """
    if len(token) <= 3 or token[-1].isdigit():
        return token
    if token.endswith("ies") and len(token) >= 5:
        token = token[:-3] + "y"
    elif token.endswith("sses") or (token.endswith("es") and len(token) >= 5):
        token = token[:-2]
    elif token.endswith("s") and not token.endswith(("ss", "us", "is")):
        token = token[:-1]
    for suffix, min_len in (("ing", 6), ("ed", 5)):
        if token.endswith(suffix) and len(token) >= min_len:
            token = token[: -len(suffix)]
            if len(token) >= 2 and token[-1] == token[-2] and token[-1] not in "lsz":
                token = token[:-1]  # planned → plan
            break
    else:
        if token.endswith("ly") and len(token) >= 6:
            token = token[:-2]
    if token.endswith("e") and len(token) >= 4:
        token = token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, stemmed and stop-worded.

    Keeps legal identifiers intact ("103(1)") — digits are never stemmed.
    Stopword removal, stemming and the layman→statutory map are applied
    identically to documents and queries so BM25 term matching stays
    symmetric.
    """
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(text.lower()):
        if raw in _STOPWORDS:
            continue
        if raw[0].isdigit():
            tokens.append(raw)  # legal identifiers ("103(1)") are never stemmed
            continue
        stem = _stem(raw)
        tokens.append(_LAYMAN_STATUTORY_MAP.get(stem, stem))
    return tokens


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
