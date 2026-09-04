"""Per-entry health/circuit state shared by every provider pool.

Each pool entry gets a ``CircuitState``: after a failure the entry "cools
down" for a classified duration (short for rate limits, long for auth
rejections) and is skipped by the router until the cooldown expires. A
success clears the failure history immediately — a single good request
proves the provider is back.

All timestamps are ``time.monotonic()`` so wall-clock jumps (NTP etc.)
cannot extend or shorten cooldowns. A custom clock can be injected for
tests.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from pydantic import BaseModel


class CircuitState(BaseModel):
    """Snapshot of one entry's circuit state (admin-visible)."""

    entry_id: str
    state: str = "untested"  # untested | healthy | cooling
    consecutive_failures: int = 0
    last_error: str = ""
    last_error_class: str = ""  # rate_limit | timeout | permanent | transient | unknown
    last_failure_at: float | None = None
    last_success_at: float | None = None
    cooling_until: float | None = None

    def is_available(self, now: float) -> bool:
        return self.cooling_until is None or now >= self.cooling_until


class HealthBoard:
    """Tracks circuit state for the entries of one pool (or all pools).

    Keyed by ``"<pool>:<entry_id>"`` so one board can serve LLM, STT and
    TTS without collisions, and admin snapshots group cleanly.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._states: dict[str, CircuitState] = {}

    # -- recording ---------------------------------------------------

    def record_success(self, pool: str, entry_id: str) -> None:
        state = self._state(pool, entry_id)
        state.state = "healthy"
        state.consecutive_failures = 0
        # A success proves the provider is back: clear any pending cooldown
        # (otherwise a recovered entry stays skipped until the timer runs
        # out even though it just served a request).
        state.cooling_until = None
        state.last_success_at = self._clock()

    def record_failure(
        self,
        pool: str,
        entry_id: str,
        *,
        error_class: str,
        message: str,
        cooldown_seconds: float,
    ) -> None:
        state = self._state(pool, entry_id)
        now = self._clock()
        state.consecutive_failures += 1
        state.last_error = message[:300]
        state.last_error_class = error_class
        state.last_failure_at = now
        # Escalating cooldown: each consecutive failure doubles the base
        # cooldown, capped — a flapping provider backs off without being
        # permanently excluded (recovery test scenario 8).
        cooldown = min(
            cooldown_seconds * (2 ** (state.consecutive_failures - 1)),
            _MAX_COOLDOWN_SECONDS,
        )
        state.cooling_until = now + cooldown
        state.state = "cooling"

    def reset(self, pool: str, entry_id: str) -> None:
        self._states.pop(f"{pool}:{entry_id}", None)

    # -- queries -----------------------------------------------------

    def is_available(self, pool: str, entry_id: str) -> bool:
        state = self._states.get(f"{pool}:{entry_id}")
        if state is None:
            return True
        return state.is_available(self._clock())

    def snapshot(self, pool: str | None = None) -> dict[str, CircuitState]:
        if pool is None:
            return dict(self._states)
        prefix = f"{pool}:"
        return {key: value for key, value in self._states.items() if key.startswith(prefix)}

    # -- internals ---------------------------------------------------

    def _state(self, pool: str, entry_id: str) -> CircuitState:
        key = f"{pool}:{entry_id}"
        if key not in self._states:
            self._states[key] = CircuitState(entry_id=entry_id)
        return self._states[key]


#: Cap for escalating cooldowns: even a permanently-broken key re-earns a
#: probe attempt hourly (admin can also disable the entry outright).
_MAX_COOLDOWN_SECONDS = 3600.0
