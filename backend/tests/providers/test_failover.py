"""Provider pool failover tests (2026-09 provider failover task).

Covers the failover engine contract with fake providers: ordering,
bounded attempts, cooldown skipping, recovery, error classification and
the stream pre-first-token rule. Integration-level behavior (routing,
persistence, admin API) lives in tests/admin/test_admin.py.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from app.core.errors import AppError, LLMRateLimitError, LLMTimeoutError
from app.providers.health import HealthBoard
from app.providers.models import (
    FailoverStrategy,
    ProviderEntryConfig,
    ProviderPoolConfig,
)
from app.providers.router import FailoverPolicy, FailoverRouter, classify_error


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeProvider:
    """Scripted provider: fails N times with an error, then succeeds."""

    def __init__(self, value: str, errors: list[BaseException] | None = None) -> None:
        self.value = value
        self._errors = list(errors or [])
        self.calls = 0

    async def generate(self, _request: Any = None) -> str:
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return self.value


def make_pool(
    entries: list[tuple[str, int, bool]], default: str | None = None, strategy: Any = None
) -> ProviderPoolConfig:
    return ProviderPoolConfig(
        entries=[
            ProviderEntryConfig(id=eid, provider="fake", priority=priority, enabled=enabled)
            for eid, priority, enabled in entries
        ],
        default_entry_id=default,
        strategy=strategy or FailoverStrategy.PRIORITY,
    )


class TestOrdering:
    def test_default_entry_first_then_priority(self) -> None:
        pool = make_pool([("a", 10, True), ("b", 1, True), ("c", 5, True)], default="a")
        assert [e.id for e in pool.ordered_entries()] == ["a", "b", "c"]

    def test_disabled_entries_never_ordered(self) -> None:
        pool = make_pool([("a", 1, False), ("b", 2, True)])
        assert [e.id for e in pool.ordered_entries()] == ["b"]

    def test_round_robin_rotates_start(self) -> None:
        pool = make_pool(
            [("a", 1, True), ("b", 2, True), ("c", 3, True)],
            strategy=FailoverStrategy.ROUND_ROBIN,
        )
        assert [e.id for e in pool.ordered_entries(0)] == ["a", "b", "c"]
        assert [e.id for e in pool.ordered_entries(1)] == ["b", "c", "a"]
        assert [e.id for e in pool.ordered_entries(3)] == ["a", "b", "c"]

    def test_default_must_be_enabled(self) -> None:
        pool = make_pool([("a", 1, False)], default="a")
        with pytest.raises(ValueError, match="disabled"):
            pool.validate_default()


class TestClassification:
    def test_rate_limit_short_cooldown(self) -> None:
        assert classify_error(LLMRateLimitError()) == ("rate_limit", 30.0)

    def test_timeout_medium_cooldown(self) -> None:
        assert classify_error(LLMTimeoutError()) == ("timeout", 60.0)

    def test_permanent_flag_long_cooldown(self) -> None:
        error = AppError("rejected")
        error.permanent = True
        assert classify_error(error) == ("permanent", 600.0)

    def test_plain_app_error_transient(self) -> None:
        assert classify_error(AppError("unavailable")) == ("transient", 60.0)

    def test_asyncio_timeout(self) -> None:
        assert classify_error(TimeoutError())[0] == "timeout"


class TestFailoverRun:
    async def test_primary_failure_falls_back_to_secondary(self) -> None:
        pool = make_pool([("a", 1, True), ("b", 2, True)])
        board = HealthBoard()
        providers = {
            "a": FakeProvider("A", [AppError("down")]),
            "b": FakeProvider("B"),
        }
        router = FailoverRouter("llm", pool, lambda e: providers[e.id], board)
        result, entry_id = await router.run(lambda p: p.generate())
        assert result == "B"
        assert entry_id == "b"
        # Primary recorded as cooling, secondary healthy.
        assert board.snapshot("llm")["llm:a"].state == "cooling"
        assert board.snapshot("llm")["llm:b"].state == "healthy"

    async def test_multiple_consecutive_failures_reach_last(self) -> None:
        pool = make_pool([("a", 1, True), ("b", 2, True), ("c", 3, True)])
        providers = {
            "a": FakeProvider("A", [AppError("1")]),
            "b": FakeProvider("B", [LLMRateLimitError()]),
            "c": FakeProvider("C"),
        }
        router = FailoverRouter("llm", pool, lambda e: providers[e.id], HealthBoard())
        result, entry_id = await router.run(lambda p: p.generate())
        assert (result, entry_id) == ("C", "c")

    async def test_all_fail_raises_last_error(self) -> None:
        pool = make_pool([("a", 1, True), ("b", 2, True)])
        providers = {
            "a": FakeProvider("A", [AppError("first")]),
            "b": FakeProvider("B", [LLMTimeoutError()]),
        }
        router = FailoverRouter("llm", pool, lambda e: providers[e.id], HealthBoard())
        with pytest.raises(LLMTimeoutError):
            await router.run(lambda p: p.generate())

    async def test_attempts_are_bounded_one_per_entry(self) -> None:
        pool = make_pool([("a", 1, True), ("b", 2, True)])
        providers = {
            "a": FakeProvider("A", [AppError("x"), AppError("y"), AppError("z")]),
            "b": FakeProvider("B", [AppError("x"), AppError("y")]),
        }
        router = FailoverRouter("llm", pool, lambda e: providers[e.id], HealthBoard())
        with pytest.raises(AppError):
            await router.run(lambda p: p.generate())
        assert providers["a"].calls == 1
        assert providers["b"].calls == 1

    async def test_empty_pool_raises(self) -> None:
        router = FailoverRouter("llm", ProviderPoolConfig(), lambda e: None, HealthBoard())
        with pytest.raises(AppError, match="pool"):
            await router.run(lambda p: None)

    async def test_cooldown_skips_entry_until_recovery(self) -> None:
        clock = FakeClock()
        board = HealthBoard(clock=clock)
        pool = make_pool([("a", 1, True), ("b", 2, True)])
        primary = FakeProvider("A", [AppError("down")])
        providers = {"a": primary, "b": FakeProvider("B")}
        router = FailoverRouter("llm", pool, lambda e: providers[e.id], board)

        await router.run(lambda p: p.generate())  # a fails → b serves
        assert providers["a"].calls == 1

        # a is cooling (60 s base): next request must NOT touch it.
        primary._errors = []
        _result, entry_id = await router.run(lambda p: p.generate())
        assert entry_id == "b"
        assert providers["a"].calls == 1  # untouched

        # Cooldown expired: a is eligible again.
        clock.advance(61.0)
        _result, entry_id = await router.run(lambda p: p.generate())
        assert entry_id == "a"
        assert providers["a"].calls == 2

    async def test_all_cooling_still_serves_least_recent_failure(self) -> None:
        clock = FakeClock()
        board = HealthBoard(clock=clock)
        pool = make_pool([("a", 1, True), ("b", 2, True)])
        providers = {
            "a": FakeProvider("A", [AppError("early")]),
            "b": FakeProvider("B"),
        }
        router = FailoverRouter("llm", pool, lambda e: providers[e.id], board)
        await router.run(lambda p: p.generate())  # a fails, b serves
        # Now b fails too — both cooling. b failed later, so a (earliest
        # failure, closest to expiry) must be retried.
        providers["b"]._errors = [AppError("late")]
        clock.advance(1.0)
        result, entry_id = await router.run(lambda p: p.generate())
        assert entry_id == "a"
        assert result == "A"

    async def test_consecutive_failures_escalate_cooldown(self) -> None:
        clock = FakeClock()
        board = HealthBoard(clock=clock)
        board.record_failure(
            "llm", "a", error_class="transient", message="x", cooldown_seconds=60.0
        )
        board.record_failure(
            "llm", "a", error_class="transient", message="x", cooldown_seconds=60.0
        )
        state = board.snapshot("llm")["llm:a"]
        assert state.consecutive_failures == 2
        assert state.cooling_until == pytest.approx(1000.0 + 120.0, rel=0.01)

    async def test_success_clears_failure_history(self) -> None:
        board = HealthBoard()
        board.record_failure(
            "llm", "a", error_class="rate_limit", message="x", cooldown_seconds=30.0
        )
        board.record_success("llm", "a")
        state = board.snapshot("llm")["llm:a"]
        assert state.state == "healthy"
        assert state.consecutive_failures == 0
        assert state.cooling_until is None

    async def test_request_deadline_bounds_chain(self) -> None:
        pool = make_pool([("a", 1, True), ("b", 2, True), ("c", 3, True)])

        class SlowProvider:
            def __init__(self, value: str) -> None:
                self.value = value

            async def generate(self, _request: Any = None) -> str:
                await asyncio.sleep(5)
                return self.value

        providers = {"a": SlowProvider("A"), "b": SlowProvider("B"), "c": SlowProvider("C")}
        policy = FailoverPolicy(request_deadline_seconds=0.1)
        router = FailoverRouter("llm", pool, lambda e: providers[e.id], HealthBoard(), policy)
        with pytest.raises((AppError, asyncio.TimeoutError, TimeoutError)):
            await router.run(lambda p: p.generate())

    async def test_round_robin_rotation_advances_on_success(self) -> None:
        pool = make_pool([("a", 1, True), ("b", 2, True)], strategy=FailoverStrategy.ROUND_ROBIN)
        providers = {"a": FakeProvider("A"), "b": FakeProvider("B")}
        router = FailoverRouter("llm", pool, lambda e: providers[e.id], HealthBoard())
        _, first = await router.run(lambda p: p.generate())
        _, second = await router.run(lambda p: p.generate())
        assert first == "a"
        assert second == "b"

    async def test_disabled_provider_never_invoked(self) -> None:
        pool = make_pool([("a", 1, False), ("b", 2, True)])
        providers = {"a": FakeProvider("A"), "b": FakeProvider("B")}
        router = FailoverRouter("llm", pool, lambda e: providers[e.id], HealthBoard())
        result, entry_id = await router.run(lambda p: p.generate())
        assert (result, entry_id) == ("B", "b")
        assert providers["a"].calls == 0


class TestFailoverStream:
    async def test_stream_fails_over_before_first_token(self) -> None:
        pool = make_pool([("a", 1, True), ("b", 2, True)])

        class StreamProvider:
            def __init__(self, chunks: list[str], error: BaseException | None = None) -> None:
                self._chunks = chunks
                self._error = error

            async def stream(self, _request: Any = None):
                if self._error is not None:
                    raise self._error
                for chunk in self._chunks:
                    yield chunk

        providers = {
            "a": StreamProvider([], AppError("down")),
            "b": StreamProvider(["tok1", "tok2"]),
        }
        router = FailoverRouter("llm", pool, lambda e: providers[e.id], HealthBoard())
        chunks = [chunk async for chunk in router.stream(lambda p: p.stream())]
        assert chunks == ["tok1", "tok2"]

    async def test_committed_stream_does_not_fail_over(self) -> None:
        pool = make_pool([("a", 1, True), ("b", 2, True)])

        class HalfwayProvider:
            def __init__(self) -> None:
                self.calls = 0

            async def stream(self, _request: Any = None):
                self.calls += 1
                yield "tok1"
                raise AppError("mid-stream failure")

        a = HalfwayProvider()
        b = HalfwayProvider()
        providers = {"a": a, "b": b}
        router = FailoverRouter("llm", pool, lambda e: providers[e.id], HealthBoard())
        collected: list[str] = []
        with pytest.raises(AppError):
            async for chunk in router.stream(lambda p: p.stream()):
                collected.append(chunk)
        assert collected == ["tok1"]
        # b was never touched: replaying a committed stream would duplicate.
        assert b.calls == 0

    async def test_stream_all_fail_raises_last(self) -> None:
        pool = make_pool([("a", 1, True), ("b", 2, True)])

        class Failing:
            async def stream(self, _request: Any = None):
                raise LLMRateLimitError()
                yield ""  # pragma: no cover

        providers = {"a": Failing(), "b": Failing()}
        router = FailoverRouter("llm", pool, lambda e: providers[e.id], HealthBoard())
        with pytest.raises(LLMRateLimitError):
            _ = [chunk async for chunk in router.stream(lambda p: p.stream())]
