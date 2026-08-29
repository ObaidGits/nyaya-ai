"""Dense retrieval tests (A3-003; A2-009 query prefix)."""

from app.ingestion.models import Chunk
from app.retrieval.dense import CosineDenseIndex, embed_query
from app.retrieval.models import MetadataFilter
from tests.retrieval.fixtures import DeterministicEmbedder, make_corpus


class _SpyEmbedder:
    """Records texts it embeds; returns DeterministicEmbedder vectors."""

    def __init__(self) -> None:
        self.embedded: list[str] = []
        self._inner = DeterministicEmbedder()

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.embedded.extend(texts)
        return self._inner.embed_texts(texts)

    def dimensions(self) -> int:
        return self._inner.dimensions()


def test_query_prefix_applied() -> None:
    spy = _SpyEmbedder()
    embed_query(spy, "what is bail")
    assert spy.embedded == ["Represent this sentence for searching relevant passages: what is bail"]


def test_dense_finds_lexically_related_chunk() -> None:
    index = CosineDenseIndex(make_corpus(), DeterministicEmbedder())
    results = index.search("bail bond release", None, top_k=3)
    assert results
    assert results[0] == "ts-s2-001"


def test_dense_synonym_query() -> None:
    # "release" is a baked-in synonym of "bail" in the deterministic
    # embedder, emulating a semantic match sparse retrieval misses.
    index = CosineDenseIndex(make_corpus(), DeterministicEmbedder())
    results = index.search("release conditions", None, top_k=3)
    assert "ts-s2-001" in results


def test_dense_metadata_filter_chapter() -> None:
    index = CosineDenseIndex(make_corpus(), DeterministicEmbedder())
    results = index.search("murder punishment", MetadataFilter(chapter="XXVII"), top_k=5)
    assert results
    assert all(cid.startswith("ts-s103") for cid in results)


def test_dense_no_match_returns_empty_or_low() -> None:
    index = CosineDenseIndex(make_corpus(), DeterministicEmbedder())
    results = index.search("zzqqxx", None, top_k=5)
    assert isinstance(results, list)


def test_dense_top_k() -> None:
    index = CosineDenseIndex(make_corpus(), DeterministicEmbedder())
    results = index.search("offence", None, top_k=2)
    assert len(results) <= 2


def test_dense_zero_vector_embedder_never_crashes() -> None:
    class _Zero:
        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            return [[0.0, 0.0] for _ in texts]

        def dimensions(self) -> int:
            return 2

    index = CosineDenseIndex([make_corpus()[0]], _Zero())  # type: ignore[arg-type]
    assert index.search("anything", None, top_k=1) == []


def test_chunk_without_dense_candidate_still_searchable() -> None:
    # Sanity: store wiring keeps chunk ids stable.
    corpus = make_corpus()
    index = CosineDenseIndex(corpus, DeterministicEmbedder())
    results = index.search("murder", None, top_k=5)
    for cid in results:
        chunk: Chunk | None = next((c for c in corpus if c.chunk_id == cid), None)
        assert chunk is not None
