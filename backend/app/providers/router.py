"""The bounded failover engine (provider-agnostic).

One ``FailoverRouter`` per capability ("llm" / "stt" / "tts"). It orders
the pool's enabled entries (default first, then priority; round-robin
rotates the start), skips entries in cooldown, invokes one at a time and
moves to the next on failure — bounded by the entry count and a
request-level deadline. No provider-specific knowledge lives here: error
*classification* is a table of exception types, so adding a provider
never touches this module.

Guarantees:
- Bounded: at most one attempt per enabled entry per request, plus the
  overall deadline — no infinite retry loops (each provider already does
  its own bounded in-provider retries for 429/5xx).
- Cooldown-aware: cooling entries are skipped; if EVERY enabled entry is
  cooling, the least-recently-failed one is tried anyway (a pool must
  never hard-fail just because timers overlap — scenario 13/8).
- Deadline: ``request_deadline_seconds`` bounds the whole failover chain;
  the per-attempt budget shrinks as time is spent (never below a floor,
  so the last entry still gets a fighting chance).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from app.core.errors import AppError, LLMRateLimitError, LLMTimeoutError
from app.providers.health import HealthBoard
from app.providers.models import ProviderEntryConfig, ProviderPoolConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Minimum per-attempt time budget once the deadline is nearly spent.
_ATTEMPT_FLOOR_SECONDS = 1.0


@dataclass(frozen=True)
class FailoverPolicy:
    """Tunables for one pool's failover behavior."""

    #: Whole-request budget across ALL entries (deadlines compound: each
    #: provider's own timeout still applies per attempt).
    request_deadline_seconds: float = 90.0
    #: Cooldown after a 429 (short — rate limits clear fast).
    rate_limit_cooldown_seconds: float = 30.0
    #: Cooldown after a timeout / connection failure / 5xx.
    transient_cooldown_seconds: float = 60.0
    #: Cooldown after a definitive rejection (auth/model/request 4xx).
    permanent_cooldown_seconds: float = 600.0


def classify_error(exc: BaseException) -> tuple[str, float]:
    """Map an exception to ``(error_class, base_cooldown_seconds)``.

    Provider-agnostic: works off the shared AppError taxonomy plus asyncio
    leftovers, and any exception carrying a truthy ``permanent`` attribute
    (set at 4xx raise sites) is treated as permanent.
    """
    if isinstance(exc, LLMRateLimitError):
        return "rate_limit", 30.0
    if isinstance(exc, LLMTimeoutError):
        return "timeout", 60.0
    if getattr(exc, "permanent", False):
        return "permanent", 600.0
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout", 60.0
    if isinstance(exc, AppError):
        # Provider down / network / config-shaped failures: retryable by
        # switching, cooled briefly.
        return "transient", 60.0
    return "unknown", 60.0


class FailoverRouter:
    """Routes one capability's requests across its pool with failover.

    ``resolver`` maps an entry config to the live provider instance
    (LLMProvider, STTProvider, ...). It is called at most once per entry
    per request and may raise — resolver failures mark the entry failed
    and fail over like any provider error.
    """

    def __init__(
        self,
        pool_name: str,
        config: ProviderPoolConfig,
        resolver: Callable[[ProviderEntryConfig], object],
        board: HealthBoard,
        policy: FailoverPolicy | None = None,
    ) -> None:
        self._pool_name = pool_name
        self._config = config
        self._resolver = resolver
        self._board = board
        self._policy = policy or FailoverPolicy()
        self._rotation = 0

    # -- public API --------------------------------------------------

    async def run(
        self,
        invoke: Callable[[object], Awaitable[T]],
    ) -> tuple[T, str]:
        """Invoke ``invoke`` against entries in failover order.

        Returns ``(result, entry_id_that_succeeded)``. Raises the last
        error when every candidate fails (or the deadline expires).
        """
        candidates = self._ordered_candidates()
        if not candidates:
            raise _no_candidates_error(self._pool_name)

        deadline = time.monotonic() + self._policy.request_deadline_seconds
        last_error: BaseException | None = None
        tried: set[str] = set()

        # The candidate list is recomputed after every failure: a failure
        # may change which entries are worth trying (all-available now
        # exhausted → least-recently-failed cooling entry as last resort).
        while True:
            candidates = [
                (entry_id, provider)
                for entry_id, provider in self._ordered_candidates()
                if entry_id not in tried
            ]
            if not candidates:
                break
            entry_id, provider = candidates[0]
            tried.add(entry_id)
            remaining = deadline - time.monotonic()
            if remaining <= 0 and len(tried) > 1:
                break
            try:
                result = await asyncio.wait_for(
                    invoke(provider),
                    timeout=max(remaining, _ATTEMPT_FLOOR_SECONDS),
                )
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 - classified below
                self._record(entry_id, exc)
                last_error = exc
                logger.warning(
                    "provider failover",
                    extra={
                        "pool": self._pool_name,
                        "entry": entry_id,
                        "error_class": classify_error(exc)[0],
                    },
                )
                continue
            self._board.record_success(self._pool_name, entry_id)
            self._bump_rotation()
            return result, entry_id

        assert last_error is not None  # no candidates raises above
        raise last_error

    def stream(
        self,
        open_stream: Callable[[object], AsyncIterator[str]],
    ) -> AsyncIterator[str]:
        """Failover variant for token streams.

        Failover happens ONLY before the first token: once the consumer
        has received anything the stream is committed and replaying it
        would duplicate output (same rule as the in-provider retry).
        """
        return self._stream(open_stream)

    async def _stream(
        self,
        open_stream: Callable[[object], AsyncIterator[str]],
    ) -> AsyncIterator[str]:
        candidates = self._ordered_candidates()
        if not candidates:
            raise _no_candidates_error(self._pool_name)

        last_error: BaseException | None = None
        tried: set[str] = set()
        while True:
            candidates = [
                (entry_id, provider)
                for entry_id, provider in self._ordered_candidates()
                if entry_id not in tried
            ]
            if not candidates:
                break
            entry_id, provider = candidates[0]
            tried.add(entry_id)
            emitted = False
            try:
                async for chunk in open_stream(provider):
                    emitted = True
                    yield chunk
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 - classified below
                self._record(entry_id, exc)
                last_error = exc
                if emitted:
                    # Committed stream: cannot fail over without
                    # duplicating tokens. Surface the error.
                    raise
                logger.warning(
                    "provider failover (stream, pre-first-token)",
                    extra={
                        "pool": self._pool_name,
                        "entry": entry_id,
                        "error_class": classify_error(exc)[0],
                    },
                )
                continue
            self._board.record_success(self._pool_name, entry_id)
            self._bump_rotation()
            return
        assert last_error is not None
        raise last_error

    # -- internals ---------------------------------------------------

    def _ordered_candidates(self) -> list[tuple[str, object]]:
        """(entry_id, provider) pairs in try-order, cooldown-aware."""
        ordered = self._config.ordered_entries(self._rotation)
        available = [
            e for e in ordered
            if self._board.is_available(self._pool_name, e.id)
        ]
        if not available:
            # Every enabled entry is cooling: pick the one that failed
            # longest ago (its cooldown is closest to expiring) rather
            # than failing outright.
            states = self._board.snapshot(self._pool_name)

            def _least_recent(entry: ProviderEntryConfig) -> float:
                state = states.get(f"{self._pool_name}:{entry.id}")
                return (
                    state.last_failure_at
                    if state and state.last_failure_at is not None
                    else 0.0
                )

            available = sorted(ordered, key=_least_recent)
        return [(e.id, self._resolve(e)) for e in available]

    def _resolve(self, entry: ProviderEntryConfig) -> object:
        try:
            return self._resolver(entry)
        except Exception as exc:
            # Resolver failure = provider cannot even be constructed
            # (unknown name, invalid config): record and let run() see a
            # fresh error for this entry by re-raising lazily.
            self._record(entry.id, exc)
            return _UnresolvableEntry(entry.id, exc)

    def _record(self, entry_id: str, exc: BaseException) -> None:
        error_class, cooldown = classify_error(exc)
        self._board.record_failure(
            self._pool_name,
            entry_id,
            error_class=error_class,
            message=str(exc),
            cooldown_seconds=cooldown,
        )

    def _bump_rotation(self) -> None:
        from app.providers.models import FailoverStrategy

        if self._config.strategy == FailoverStrategy.ROUND_ROBIN:
            self._rotation += 1


class _UnresolvableEntry:
    """Placeholder for an entry whose provider failed to construct.

    Invoking it raises the construction error, which the router then
    records and fails over from — so a bad pool entry can never wedge a
    request.
    """

    def __init__(self, entry_id: str, error: Exception) -> None:
        self._entry_id = entry_id
        self._error = error

    def __getattr__(self, name: str) -> object:
        def _raise(*_args: object, **_kwargs: object) -> object:
            raise self._error

        return _raise


def _no_candidates_error(pool_name: str) -> AppError:
    return AppError(
        f"No enabled provider is configured for the {pool_name.upper()} pool.",
        status_code=503,
        code=f"{pool_name.upper()}_POOL_EMPTY",
    )
