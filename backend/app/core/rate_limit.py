"""In-memory per-session rate limiting (REQUIREMENTS D-049/D-050; ARCHITECTURE §48).

A sliding-window counter keyed by session identity. Two independent budgets:
chat requests and document uploads. Exceeding a budget raises a 429 with the
standard error envelope. The limiter is process-local state attached to the
application (single-process deployment; no shared store is in scope).
"""

from __future__ import annotations

import threading
import time
from collections import deque

from app.core.errors import AppError

CHAT_SCOPE = "chat"
UPLOAD_SCOPE = "upload"
SPEECH_SCOPE = "speech"
SEARCH_SCOPE = "search"
FEEDBACK_SCOPE = "feedback"
ADMIN_LOGIN_SCOPE = "admin_login"


class RateLimitExceededError(AppError):
    """The session has exceeded its request budget (D-049/D-050)."""

    status_code = 429
    code = "RATE_LIMITED"


class RateLimiter:
    """Thread-safe sliding-window limiter for named scopes.

    ``max_tracked_keys`` bounds the key map: once it is reached, entries whose
    every event is older than their own window are evicted. Without this, the
    map leaks one deque per abandoned key forever (e.g. attacker-rotated
    session ids), so memory grows with request history. Entries are also
    dropped opportunistically when an allow() call finds them fully expired.
    """

    def __init__(self, max_tracked_keys: int = 4096) -> None:
        self._lock = threading.Lock()
        self._events: dict[tuple[str, str], deque[float]] = {}
        self._windows: dict[tuple[str, str], float] = {}
        self._max_tracked_keys = max(1, max_tracked_keys)

    def allow(self, scope: str, key: str, limit: int, window_seconds: float) -> bool:
        """Record one event and report whether it was within budget."""
        now = time.monotonic()
        entry = (scope, key)
        with self._lock:
            events = self._events.setdefault(entry, deque())
            self._windows[entry] = window_seconds
            cutoff = now - window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                if not events:
                    # limit <= 0: the entry would otherwise sit empty forever.
                    del self._events[entry]
                    self._windows.pop(entry, None)
                return False
            events.append(now)
            if len(self._events) >= self._max_tracked_keys:
                self._evict_expired(now)
            return True

    def _evict_expired(self, now: float) -> None:
        """Remove entries with no live events (their whole window elapsed)."""
        stale = [
            entry
            for entry, events in self._events.items()
            if not events or events[-1] <= now - self._windows.get(entry, 0.0)
        ]
        for entry in stale:
            del self._events[entry]
            self._windows.pop(entry, None)

    def reset(self) -> None:
        """Drop all recorded events (test seam)."""
        with self._lock:
            self._events.clear()
            self._windows.clear()


def enforce_rate_limit(
    limiter: RateLimiter, *, scope: str, key: str, limit: int, window_seconds: float
) -> None:
    """Raise the 429 application error when the budget is exhausted."""
    if not limiter.allow(scope, key, limit, window_seconds):
        raise RateLimitExceededError(
            "Too many requests. Please wait a moment and try again.",
        )
