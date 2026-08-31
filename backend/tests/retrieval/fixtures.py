"""Shared retrieval test fixtures.

A small synthetic statute corpus plus deterministic dense-retriever stubs.
The corpus is fictional; it exists to exercise every retrieval pathway
(dense-only, sparse-only, overlap, lookup, filters, empty results)
without depending on the pending real BNS source.
"""

from __future__ import annotations

import hashlib
import math
import re

from app.ingestion.models import Chunk
from app.retrieval.models import MetadataFilter
from app.retrieval.sparse import tokenize

ACT = "Test Sanhita, 2023"
ACT_SHORT = "TS"


def _chunk(
    chunk_id: str,
    section: str,
    title: str,
    text: str,
    *,
    chapter: str = "I",
    chapter_title: str = "TEST",
    subsection: str | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        act=ACT,
        act_short=ACT_SHORT,
        chapter=chapter,
        chapter_title=chapter_title,
        section_number=section,
        section_title=title,
        subsection=subsection,
        clause=None,
        text=text,
        has_illustration=False,
        has_proviso=False,
        has_exception=False,
        page_start=1,
        page_end=1,
        source_uri="pdf:sha256-test#page=1",
        ingested_at="2026-08-30T00:00:00Z",
    )


def make_corpus() -> list[Chunk]:
    """Five chunks covering every retrieval pathway."""
    return [
        _chunk(
            "ts-s1-001",
            "1",
            "Short title",
            "This Act may be called the Test Sanhita, 2023. It extends to the whole of India.",
        ),
        _chunk(
            "ts-s2-001",
            "2",
            "Definitions",
            "In this Sanhita, unless the context otherwise requires, bail means release "
            "on bond, and cognizable offence has the meaning assigned to it.",
        ),
        _chunk(
            "ts-s103-001",
            "103",
            "Punishment for murder",
            "Whoever commits murder shall be punished with death or imprisonment for life.",
            chapter="XXVII",
            chapter_title="OFFENCES AGAINST LIFE",
        ),
        _chunk(
            "ts-s103-002",
            "103",
            "Punishment for murder",
            "Provided that where a person is under eighteen years, the penalty is reduced.",
            chapter="XXVII",
            chapter_title="OFFENCES AGAINST LIFE",
            subsection="(1)",
        ),
        _chunk(
            "ts-s9-001",
            "9",
            "Rash driving",
            "Whoever drives a motor vehicle at a speed dangerous to the public is guilty "
            "of an offence.",
            chapter="XVII",
            chapter_title="OFFENCES AGAINST SAFETY",
        ),
    ]


class FakeDenseRetriever:
    """Scripted dense retriever: exact query → prescribed ranking.

    Emulates semantic matches BM25 cannot see (synonym phrasing) so
    dense-only candidates and overlap candidates are fully controlled.
    """

    def __init__(self, mapping: dict[str, list[str]]) -> None:
        self._mapping = mapping

    def search(self, query: str, flt: MetadataFilter | None, top_k: int) -> list[str]:
        return self._mapping.get(query, [])[:top_k]


class DeterministicEmbedder:
    """Hashing bag-of-words embedder (normalized, fixed dims).

    Deterministic, dependency-free stand-in for the real BGE model in
    tests: cosine similarity approximates lexical overlap. NOT a model
    quality claim; production retrieval uses BgeEmbedder.
    """

    def __init__(self, dimensions: int = 256) -> None:
        self._dimensions = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def dimensions(self) -> int:
        return self._dimensions

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dimensions
        for token in tokenize(text):
            if token == "bail":
                token = "release"  # single baked-in synonym for NL tests
            digest = hashlib.sha256(token.encode()).digest()
            vec[int.from_bytes(digest[:4], "big") % self._dimensions] += 1.0
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec


def normalize_query(query: str) -> str:
    """Lowercase + strip punctuation for scripted-retriever lookups."""
    return re.sub(r"\s+", " ", query.strip().lower())
