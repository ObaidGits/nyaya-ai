"""Redis document-index hardening tests (H8a TTL, H8b batching/decode, H8c orphan delete).

Uses in-memory fakes (no live Redis; Redis only exists inside the docker
network). ``BytesRedis`` simulates a ``decode_responses=False`` client to
pin the decode contract; ``FakeRedis`` (str mode) covers the happy paths.
"""

from __future__ import annotations

from typing import Any

from app.documents.redis_index import (
    _DEFAULT_TTL_SECONDS,
    RedisDocumentIndex,
)
from app.documents.retrieval import DocumentRetrievalService
from app.ingestion.embeddings import HashingEmbedder

SESSION = "redis-index-session-1"
OTHER = "redis-index-session-2"
NOTICE = "The tenant must give 30 days written notice before vacating."


class FakePipeline:
    def __init__(self, redis: FakeRedis | BytesRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[Any, ...]]] = []

    def hset(self, key: Any, field: Any, value: Any) -> None:
        self._ops.append(("hset", (key, field, value)))

    def hdel(self, key: Any, *fields: Any) -> None:
        self._ops.append(("hdel", (key, fields)))

    def set(self, key: Any, value: Any) -> None:
        self._ops.append(("set", (key, value)))

    def delete(self, key: Any) -> None:
        self._ops.append(("del", (key,)))

    def expire(self, key: Any, seconds: int) -> None:
        self._ops.append(("expire", (key, seconds)))

    def execute(self) -> None:
        for op, args in self._ops:
            if op == "hset":
                key, field, value = args
                self._redis.hset(key, field, value)
            elif op == "hdel":
                key, fields = args
                self._redis.hdel(key, *fields)
            elif op == "set":
                key, value = args
                self._redis.set(key, value)
            elif op == "del":
                (key,) = args
                self._redis.delete(key)
            elif op == "expire":
                self._redis.expire(*args)
        self._ops = []


class _BaseFakeRedis:
    def __init__(self) -> None:
        self.hash_store: dict[Any, dict[Any, Any]] = {}
        self.kv: dict[Any, Any] = {}
        self.expire_calls: list[tuple[Any, int]] = []
        self.hgetall_calls: list[Any] = []

    def hset(self, key: Any, field: Any, value: Any) -> None:
        self.hash_store.setdefault(key, {})[field] = value

    def hdel(self, key: Any, *fields: Any) -> None:
        for field in fields:
            self.hash_store.get(key, {}).pop(field, None)

    def hgetall(self, key: Any) -> dict[Any, Any]:
        self.hgetall_calls.append(key)
        return dict(self.hash_store.get(key, {}))

    def hget(self, key: Any, field: Any) -> Any:
        return self.hash_store.get(key, {}).get(field)

    def set(self, key: Any, value: Any) -> None:
        self.kv[key] = value

    def get(self, key: Any) -> Any:
        return self.kv.get(key)

    def delete(self, key: Any) -> None:
        self.kv.pop(key, None)

    def expire(self, key: Any, seconds: int) -> None:
        self.expire_calls.append((key, seconds))

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)


class FakeRedis(_BaseFakeRedis):
    """decode_responses=True-style client: all values are str."""


class BytesRedis(_BaseFakeRedis):
    """decode_responses=False-style client: hashes/kvs return bytes."""

    def hgetall(self, key: Any) -> dict[Any, Any]:
        self.hgetall_calls.append(key)
        raw = self.hash_store.get(key, {})
        return {
            k.encode() if isinstance(k, str) else k: (v.encode() if isinstance(v, str) else v)
            for k, v in raw.items()
        }

    def hget(self, key: Any, field: Any) -> Any:
        value = self.hash_store.get(key, {}).get(field)
        return value.encode() if isinstance(value, str) else value

    def get(self, key: Any) -> Any:
        value = self.kv.get(key)
        return value.encode() if isinstance(value, str) else value


class TestUpsertTTL:
    """H8a: session keys and docindex key carry a sliding TTL."""

    def test_upsert_sets_ttl_on_all_keys_with_default(self) -> None:
        fake = FakeRedis()
        index = RedisDocumentIndex(fake)  # type: ignore[arg-type]
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, [1.0, 0.0])])
        expires = dict(fake.expire_calls)
        assert expires == {
            "nyaya:docvectors:" + SESSION: _DEFAULT_TTL_SECONDS,
            "nyaya:doctexts:" + SESSION: _DEFAULT_TTL_SECONDS,
            "nyaya:docindex:d1": _DEFAULT_TTL_SECONDS,
        }

    def test_upsert_ttl_is_configurable(self) -> None:
        fake = FakeRedis()
        index = RedisDocumentIndex(fake, ttl_seconds=3600)  # type: ignore[arg-type]
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, [1.0, 0.0])])
        assert fake.expire_calls
        assert all(ttl == 3600 for _key, ttl in fake.expire_calls)

    def test_ttl_refreshed_on_every_upsert_sliding(self) -> None:
        fake = FakeRedis()
        index = RedisDocumentIndex(fake, ttl_seconds=60)  # type: ignore[arg-type]
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, [1.0, 0.0])])
        first = len(fake.expire_calls)
        index.upsert(SESSION, "d2", [("d2-p1-000", "other", [0.0, 1.0])])
        assert len(fake.expire_calls) == 2 * first  # refreshed, not one-shot

    def test_ttl_zero_disables_expiry(self) -> None:
        fake = FakeRedis()
        index = RedisDocumentIndex(fake, ttl_seconds=0)  # type: ignore[arg-type]
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, [1.0, 0.0])])
        assert fake.expire_calls == []

    def test_settings_default_ttl_matches(self) -> None:
        from app.core.config import Settings

        assert Settings(_env_file=None).document_session_ttl_seconds == _DEFAULT_TTL_SECONDS


class TestTextsBatching:
    """H8b: one HGETALL for the whole session's texts, decoded to str."""

    def test_texts_returns_full_dict_decoded(self) -> None:
        fake = FakeRedis()
        index = RedisDocumentIndex(fake)  # type: ignore[arg-type]
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, [1.0, 0.0])])
        index.upsert(SESSION, "d2", [("d2-p1-000", "other", [0.0, 1.0])])
        assert index.texts(SESSION) == {"d1-p1-000": NOTICE, "d2-p1-000": "other"}

    def test_texts_single_round_trip(self) -> None:
        fake = FakeRedis()
        index = RedisDocumentIndex(fake)  # type: ignore[arg-type]
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, [1.0, 0.0])])
        fake.hgetall_calls.clear()
        assert index.texts(SESSION)
        assert fake.hgetall_calls == ["nyaya:doctexts:" + SESSION]

    def test_get_text_decodes_bytes(self) -> None:
        fake = BytesRedis()
        index = RedisDocumentIndex(fake)  # type: ignore[arg-type]
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, [1.0, 0.0])])
        text = index.get_text(SESSION, "d1-p1-000")
        assert text == NOTICE
        assert isinstance(text, str)

    def test_texts_decodes_bytes_keys_and_values(self) -> None:
        fake = BytesRedis()
        index = RedisDocumentIndex(fake)  # type: ignore[arg-type]
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, [1.0, 0.0])])
        texts = index.texts(SESSION)
        assert texts == {"d1-p1-000": NOTICE}
        assert all(isinstance(key, str) for key in texts)
        assert all(isinstance(value, str) for value in texts.values())

    def test_lexical_pass_is_one_batched_fetch(self) -> None:
        """retrieval.py must not do N sequential per-chunk HGETs."""
        fake = FakeRedis()
        index = RedisDocumentIndex(fake)  # type: ignore[arg-type]
        embedder = HashingEmbedder()
        vector = embedder.embed_texts([NOTICE])[0]
        index.upsert(
            SESSION,
            "d1",
            [(f"d1-p0001-{i:03d}", NOTICE, vector) for i in range(5)],
        )
        fake.hgetall_calls.clear()
        service = DocumentRetrievalService(index, embedder)  # type: ignore[arg-type]
        evidence = service.retrieve(SESSION, "30 days written notice")
        assert evidence.hits
        # Exactly ONE texts-hash HGETALL for the whole lexical pass.
        texts_key = "nyaya:doctexts:" + SESSION
        assert fake.hgetall_calls.count(texts_key) == 1


class TestDeleteOrphanRecovery:
    """H8c: missing docindex key must not orphan vectors/texts."""

    def test_delete_recovers_chunks_when_docindex_missing(self) -> None:
        fake = FakeRedis()
        index = RedisDocumentIndex(fake)  # type: ignore[arg-type]
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, [1.0, 0.0])])
        # Simulate the doc-chunk map expiring/being lost.
        fake.kv.pop("nyaya:docindex:d1", None)
        removed = index.delete(SESSION, "d1")
        assert removed == 1
        assert index.search(SESSION, [1.0, 0.0], top_k=5) == []
        assert index.get_text(SESSION, "d1-p1-000") is None
        assert index.texts(SESSION) == {}

    def test_delete_only_purges_the_target_document_chunks(self) -> None:
        fake = FakeRedis()
        index = RedisDocumentIndex(fake)  # type: ignore[arg-type]
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, [1.0, 0.0])])
        index.upsert(SESSION, "d2", [("d2-p1-000", "keep me", [0.0, 1.0])])
        fake.kv.pop("nyaya:docindex:d1", None)
        assert index.delete(SESSION, "d1") == 1
        # d2 untouched despite the prefix-scan recovery path.
        assert index.get_text(SESSION, "d2-p1-000") == "keep me"
        assert [cid for cid, _ in index.search(SESSION, [0.0, 1.0], top_k=5)] == ["d2-p1-000"]

    def test_delete_missing_document_still_returns_zero(self) -> None:
        fake = FakeRedis()
        index = RedisDocumentIndex(fake)  # type: ignore[arg-type]
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, [1.0, 0.0])])
        assert index.delete(SESSION, "no-such-doc") == 0
        assert index.get_text(SESSION, "d1-p1-000") == NOTICE

    def test_delete_docindex_missing_and_no_chunks_returns_zero(self) -> None:
        fake = FakeRedis()
        index = RedisDocumentIndex(fake)  # type: ignore[arg-type]
        assert index.delete(SESSION, "ghost") == 0


class TestSearchSessionIsolation:
    def test_search_is_session_isolated_with_bytes_client(self) -> None:
        fake = BytesRedis()
        index = RedisDocumentIndex(fake)  # type: ignore[arg-type]
        index.upsert(SESSION, "d1", [("d1-p1-000", NOTICE, [1.0, 0.0])])
        index.upsert(OTHER, "d2", [("d2-p1-000", "foreign", [1.0, 0.0])])
        hits = index.search(SESSION, [1.0, 0.0], top_k=5)
        assert [chunk_id for chunk_id, _score in hits] == ["d1-p1-000"]
