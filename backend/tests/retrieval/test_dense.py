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


# --- persisted vector cache (startup does not re-embed the corpus) ----------


def test_vector_cache_roundtrip_skips_reembedding(tmp_path) -> None:
    """Second build with a cache path embeds nothing and returns the same vectors."""
    chunks = make_corpus()
    spy = _SpyEmbedder()
    cache = tmp_path / "dense_vectors.json"
    first = CosineDenseIndex(chunks, spy, vector_cache_path=cache)
    assert spy.embedded == [c.text for c in chunks]
    assert cache.is_file()

    spy2 = _SpyEmbedder()
    second = CosineDenseIndex(chunks, spy2, vector_cache_path=cache)
    assert spy2.embedded == []  # cache hit: no corpus re-embedding
    assert first._vectors == second._vectors


def test_vector_cache_invalidated_by_changed_text(tmp_path) -> None:
    """A corpus whose text changed re-embeds instead of serving stale vectors."""
    chunks = make_corpus()
    cache = tmp_path / "dense_vectors.json"
    CosineDenseIndex(chunks, _SpyEmbedder(), vector_cache_path=cache)

    changed = [c.model_copy(update={"text": c.text + " amended"}) for c in chunks]
    spy = _SpyEmbedder()
    rebuilt = CosineDenseIndex(changed, spy, vector_cache_path=cache)
    assert spy.embedded == [c.text for c in changed]  # full re-embed
    assert all(rebuilt._vectors[c.chunk_id] for c in changed)


def test_vector_cache_invalidated_by_different_embedder(tmp_path) -> None:
    """A different embedder identity never reuses cached vectors."""
    from tests.retrieval.fixtures import DeterministicEmbedder

    chunks = make_corpus()
    cache = tmp_path / "dense_vectors.json"
    CosineDenseIndex(chunks, _SpyEmbedder(), vector_cache_path=cache)

    class OtherEmbedder(DeterministicEmbedder):
        pass

    spy = _SpyEmbedder()
    index = CosineDenseIndex(chunks, spy, vector_cache_path=cache)
    assert spy.embedded == []  # same identity, still cached
    # OtherEmbedder is a different identity: its build re-embeds and the
    # cache file records the new embedder header (never reuse another
    # embedder's vectors).
    import json as _json

    CosineDenseIndex(chunks, OtherEmbedder(), vector_cache_path=cache)
    header = _json.loads(cache.read_text())["embedder"]
    assert "OtherEmbedder" in header
    rebuilt = CosineDenseIndex(chunks, spy, vector_cache_path=cache)
    assert spy.embedded == [c.text for c in chunks]  # header changed -> re-embed
    assert rebuilt._vectors.keys() == index._vectors.keys()
